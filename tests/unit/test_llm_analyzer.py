"""Schema validation and source-grounding are mandatory before accepting claims."""

import copy
import io
import json

import httpx
import pytest
from docx import Document
from pydantic import ValidationError

from backend.core.schemas import Finding
from backend.documents.parser import parse_document
from backend.llm.analyzer import analyze_document, extract_fragments, grounded_findings
from conftest import mock_chat_completion, mock_semantic_payload
from samples.generate import build_document


def sample():
    return parse_document(build_document("semantic_conflict"))


def test_cross_section_evidence_and_ids_are_stable():
    parsed = sample()
    fragments = extract_fragments(parsed)
    payload = mock_semantic_payload(fragments)
    result = grounded_findings(json.dumps(payload), fragments, parsed.sha256)
    assert len(result) == 1
    finding = result[0]
    assert finding.source == "llm" and finding.category == "semantic" and not finding.auto_fix_allowed
    assert len(set(finding.locations)) == 2 and all(location in parsed.node_map for location in finding.locations)
    sent = {f.element_id: f.text for f in fragments}
    assert all(claim["quote"] in sent[claim["element_id"]] for claim in finding.actual["claims"])
    assert "10 календарных" in finding.evidence and "30 календарных" in finding.evidence
    assert grounded_findings(json.dumps(payload), fragments, parsed.sha256)[0].id == finding.id


@pytest.mark.parametrize("mutation,code", [("unknown_id", "invalid_llm_reference"),
                                          ("invented_quote", "invalid_llm_evidence"),
                                          ("duplicate_id", "invalid_llm_reference"),
                                          ("same_section", "invalid_llm_reference")])
def test_hallucinated_or_invalid_evidence_is_rejected(llm_settings, mutation, code):
    parsed = sample()
    fragments = extract_fragments(parsed)
    payload = mock_semantic_payload(fragments)
    references = payload["issues"][0]["references"]
    if mutation == "unknown_id":
        references[0]["element_id"] = "word/document.xml:/invented"
    elif mutation == "invented_quote":
        references[0]["quote"] = "This text does not exist in the document."
    elif mutation == "duplicate_id":
        references[1]["element_id"] = references[0]["element_id"]
    else:
        references[1] = {"element_id": fragments[0].element_id, "quote": fragments[0].text}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_chat_completion(json.dumps(payload))))
    findings, metadata = analyze_document(parsed, llm_settings, "ollama", transport=transport)
    assert not findings and metadata.status == "failed" and metadata.error_code == code
    assert metadata.json_valid is True and metadata.references_valid is False


@pytest.mark.parametrize("content", ["not JSON", "```json\n{\"issues\":[]}\n```", '{"issues":"wrong-type"}',
                                     '{"issues":[],"auto_fix_allowed":true}'])
def test_invalid_schema_or_editing_instructions_are_rejected(llm_settings, content):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_chat_completion(content)))
    findings, metadata = analyze_document(sample(), llm_settings, "vllm", transport=transport)
    assert not findings and metadata.error_code == "invalid_llm_json" and metadata.json_valid is False


def test_duplicate_claims_are_not_published_twice():
    parsed = sample()
    fragments = extract_fragments(parsed)
    payload = mock_semantic_payload(fragments)
    duplicate = copy.deepcopy(payload["issues"][0])
    duplicate["references"].reverse()
    payload["issues"].append(duplicate)
    assert len(grounded_findings(json.dumps(payload), fragments, parsed.sha256)) == 1


def test_empty_result_is_valid_but_not_a_quality_guarantee(llm_settings):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_chat_completion('{"issues":[]}')))
    findings, metadata = analyze_document(sample(), llm_settings, "ollama", transport=transport)
    assert not findings and metadata.status == "completed"
    assert metadata.json_valid and metadata.references_valid


def test_input_limit_does_not_silently_truncate_or_send_data(llm_settings):
    llm_settings.llm_max_input_chars = 1024
    document = Document(io.BytesIO(build_document("semantic_conflict")))
    document.paragraphs[1].add_run(" Синтетические дополнительные данные." * 100)
    result = io.BytesIO()
    document.save(result)

    def must_not_send(request):
        pytest.fail("Oversized input must not leave the process")

    findings, metadata = analyze_document(parse_document(result.getvalue()), llm_settings, "ollama",
                                          transport=httpx.MockTransport(must_not_send))
    assert not findings and metadata.error_code == "llm_input_too_large" and metadata.attempts == 0


def test_one_section_is_skipped_without_network(llm_settings):
    document = Document()
    document.add_paragraph("Only one synthetic section is available.")
    result = io.BytesIO()
    document.save(result)

    def must_not_send(request):
        pytest.fail("One section must not trigger a model call")

    findings, metadata = analyze_document(parse_document(result.getvalue()), llm_settings, "ollama",
                                          transport=httpx.MockTransport(must_not_send))
    assert not findings and metadata.status == "skipped" and metadata.error_code == "insufficient_sections"


def test_llm_finding_cannot_grant_itself_edit_permission():
    parsed = sample()
    fragments = extract_fragments(parsed)
    finding = grounded_findings(json.dumps(mock_semantic_payload(fragments)), fragments, parsed.sha256)[0]
    with pytest.raises(ValidationError):
        Finding.model_validate({**finding.model_dump(), "auto_fix_allowed": True})
    with pytest.raises(ValidationError):
        Finding.model_validate({**finding.model_dump(), "status": "fixed"})
