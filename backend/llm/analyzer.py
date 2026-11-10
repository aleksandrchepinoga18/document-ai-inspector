"""Ground model claims in exactly the supported fragments that were sent."""

import json
import time
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from backend.core.config import Settings
from backend.core.schemas import Finding, LLMAnalysis, LLMValidationProblem
from backend.documents.parser import ParsedDocument
from backend.llm.errors import LLMError
from backend.llm.prompts import PROMPT_VERSION
from backend.llm.providers import create_provider
from backend.llm.schemas import Fragment, SemanticResponse


def extract_fragments(parsed: ParsedDocument) -> list[Fragment]:
    section_id = parsed.elements[0].id
    result = []
    blockers = [parsed.node_map[f.element_id] for f in parsed.unsupported if f.feature != "additional_story"]
    for item in parsed.elements:
        if item.kind not in {"paragraph", "heading"}:
            continue
        if item.kind == "heading" and item.properties.get("top_level"):
            section_id = item.id
        if not item.text.strip():
            continue
        node = parsed.node_map[item.id]
        if any(block is node or block in node.iterancestors() or node in block.iterancestors() for block in blockers):
            continue
        result.append(Fragment(element_id=item.id, section_id=section_id, text=item.text))
    return result


def grounded_findings(content: str, fragments: list[Fragment], sha256: str) -> list[Finding]:
    response = SemanticResponse.model_validate_json(content)
    sent = {f.element_id: f for f in fragments}
    findings, seen = [], set()
    def reject(code, message, reason, issue_index, reference_index=None):
        raise LLMError(code, message, diagnostic=LLMValidationProblem(
            issue_index=issue_index, reference_index=reference_index, reason=reason))

    for issue_index, issue in enumerate(response.issues):
        ids = [r.element_id for r in issue.references]
        if len(set(ids)) != len(ids):
            reject("invalid_llm_reference", "Ответ ссылается на повторный фрагмент.", "duplicate_id", issue_index)
        for reference_index, identifier in enumerate(ids):
            if identifier not in sent:
                reject("invalid_llm_reference", "Ответ ссылается на неизвестный фрагмент.",
                       "unknown_id", issue_index, reference_index)
        if len({sent[identifier].section_id for identifier in ids}) < 2:
            reject("invalid_llm_reference", "Для противоречия нужны цитаты из разных разделов.",
                   "same_section", issue_index)
        for reference_index, reference in enumerate(issue.references):
            if len(reference.quote.strip()) < 8 or not any(c.isalnum() for c in reference.quote):
                reject("invalid_llm_evidence", "Ответ содержит неинформативную цитату.",
                       "uninformative_quote", issue_index, reference_index)
            if reference.quote not in sent[reference.element_id].text:
                reject("invalid_llm_evidence", "Цитата модели не совпадает с текстом указанного фрагмента.",
                       "quote_not_found", issue_index, reference_index)
        key = json.dumps(sorted((r.element_id, r.quote) for r in issue.references), ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        findings.append(Finding(
            id=uuid5(NAMESPACE_URL, f"{sha256}:{PROMPT_VERSION}:{key}"),
            rule_id="semantic_contradiction", category="semantic", severity=issue.severity,
            element_id=ids[0], locations=ids,
            evidence="\n".join(f"{r.element_id}: {r.quote}" for r in issue.references),
            actual={"claims": [r.model_dump() for r in issue.references], "explanation": issue.explanation},
            expected="Согласованные утверждения об одном предмете в разных разделах.",
            source="llm", suggestion=issue.suggestion, auto_fix_allowed=False,
        ))
    return findings


def analyze_document(parsed: ParsedDocument, settings: Settings, provider_name: str, *,
                     allow_cloud: bool = False, synthetic_data: bool = False, transport=None) -> tuple[list[Finding], LLMAnalysis]:
    started = time.monotonic()
    metadata = LLMAnalysis(status="failed", provider=provider_name, prompt_version=PROMPT_VERSION)
    try:
        # Consent/configuration is checked before constructing an HTTP client.
        provider = create_provider(provider_name, settings, allow_cloud=allow_cloud,
                                   synthetic_data=synthetic_data, transport=transport)
        metadata.model = provider.model
        fragments = extract_fragments(parsed)
        metadata.fragment_count = len(fragments)
        if len({f.section_id for f in fragments}) < 2:
            metadata.status = "skipped"
            metadata.error_code = "insufficient_sections"
            metadata.message = "Недостаточно поддерживаемых фрагментов из двух разделов; LLM не вызывалась."
            return [], metadata
        serialized = json.dumps([f.model_dump() for f in fragments], ensure_ascii=False)
        if len(fragments) > settings.llm_max_fragments or len(serialized) > settings.llm_max_input_chars:
            raise LLMError("llm_input_too_large", "Текст превышает лимит LLM; автоматическое усечение отключено.")
        reply = provider.analyze(fragments)
        metadata.attempts = reply.attempts
        credential = getattr(settings, provider_name + "_api_key").get_secret_value()
        if credential and credential in reply.content:
            raise LLMError("llm_sensitive_response", "Ответ провайдера содержит служебные данные и отклонён.")
        try:
            # Pydantic validates JSON shape independently of the provider's JSON mode.
            validated_response = SemanticResponse.model_validate_json(reply.content)
            metadata.json_valid = True
        except ValidationError as exc:
            metadata.json_valid = False
            raise LLMError("invalid_llm_json", "Модель вернула JSON, не соответствующий схеме.") from exc
        if credential and credential in validated_response.model_dump_json():
            raise LLMError("llm_sensitive_response", "Ответ провайдера содержит служебные данные и отклонён.")
        metadata.response_issue_count = len(validated_response.issues)
        try:
            findings = grounded_findings(reply.content, fragments, parsed.sha256)
            metadata.references_valid = True
            metadata.discarded_issue_count = 0
        except LLMError as exc:
            metadata.references_valid = False
            # The existing all-or-nothing contract discards the whole response.
            metadata.discarded_issue_count = metadata.response_issue_count
            if exc.diagnostic is not None:
                metadata.validation_diagnostics.append(exc.diagnostic)
            raise
        metadata.status = "completed"
        return findings, metadata
    except LLMError as exc:
        metadata.error_code = exc.code
        metadata.message = exc.message
        metadata.attempts = max(metadata.attempts, exc.attempts)
        return [], metadata
    except Exception:
        metadata.error_code = "llm_failed"
        metadata.message = "Не удалось выполнить LLM-анализ; формальные результаты сохранены."
        return [], metadata
    finally:
        metadata.duration_seconds = round(time.monotonic() - started, 4)
