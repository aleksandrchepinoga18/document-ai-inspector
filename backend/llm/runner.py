"""Hard LLM deadline independent from the completed deterministic worker."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from backend.core.config import Settings
from backend.core.schemas import Finding, LLMAnalysis
from backend.documents.errors import DocumentError
from backend.llm.prompts import PROMPT_VERSION
from backend.llm.providers import SECRET_FIELDS


def validate_selection(use_llm: bool, provider: str, allow_cloud: bool = False, synthetic_data: bool = False):
    if not use_llm:
        if provider != "none" or allow_cloud:
            raise DocumentError("unsupported_mode", "При use_llm=false задайте provider=none и allow_cloud=false.")
        return
    if provider not in {"ollama", "vllm", "yandex"}:
        raise DocumentError("unknown_provider", "Явно выберите provider=ollama, vllm или yandex.")
    if provider == "yandex" and not (allow_cloud and synthetic_data):
        raise DocumentError("cloud_consent_required", "Yandex разрешён только с allow_cloud=true и synthetic_data=true.", 403)


def run_llm_analysis(original: Path, settings: Settings, provider: str, *, allow_cloud=False,
                     synthetic_data=False, timeout: float) -> tuple[list[Finding], LLMAnalysis]:
    validate_selection(True, provider, allow_cloud, synthetic_data)
    started = time.monotonic()
    metadata = LLMAnalysis(status="failed", provider=provider, prompt_version=PROMPT_VERSION)
    budget = min(timeout, settings.llm_total_timeout_seconds)
    if budget <= 0:
        metadata.error_code = "llm_timeout"
        metadata.message = "Нет времени для LLM; формальные результаты сохранены."
        return [], metadata
    safe_settings = settings.model_dump(mode="json", exclude=SECRET_FIELDS)
    request = {"original_path": str(original), "settings": safe_settings, "provider": provider,
               "allow_cloud": allow_cloud, "synthetic_data": synthetic_data}
    environment = {name: value for name, value in os.environ.items() if name.lower() not in SECRET_FIELDS}
    selected_key = provider + "_api_key"
    environment[selected_key.upper()] = getattr(settings, selected_key).get_secret_value()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "backend.llm.worker"], input=json.dumps(request, ensure_ascii=True),
            text=True, encoding="utf-8", capture_output=True, env=environment,
            cwd=Path(__file__).resolve().parents[2], timeout=budget, check=False,
        )
        if result.returncode != 0:
            raise ValueError("Worker failed")
        payload = json.loads(result.stdout)
        metadata = LLMAnalysis.model_validate(payload["llm"])
        findings = [Finding.model_validate(f) for f in payload["findings"]]
        if metadata.provider != provider or any(f.source != "llm" or f.auto_fix_allowed for f in findings):
            raise ValueError("Invalid LLM worker contract")
        if metadata.status != "completed" and findings:
            raise ValueError("Failed LLM phase cannot publish findings")
        if metadata.status == "completed" and not (metadata.json_valid and metadata.references_valid):
            raise ValueError("Completed LLM phase must pass schema and reference validation")
        return findings, metadata
    except subprocess.TimeoutExpired:
        metadata.attempts = None  # The killed worker may have already sent an HTTP request.
        metadata.error_code = "llm_timeout"
        metadata.message = "LLM-процесс остановлен по timeout; формальные результаты сохранены."
        return [], metadata
    except Exception:
        metadata = LLMAnalysis(
            status="failed", provider=provider, prompt_version=PROMPT_VERSION,
            error_code="llm_failed", message="Сбой LLM-процесса; формальные результаты сохранены.",
            attempts=None,
        )
        return [], metadata
    finally:
        metadata.duration_seconds = round(time.monotonic() - started, 4)
