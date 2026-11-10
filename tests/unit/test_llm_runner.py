"""Hard timeout and credential handling do not depend on live endpoints."""

import json
import subprocess

from backend.core.schemas import LLMAnalysis
from backend.llm.runner import run_llm_analysis


def test_secrets_are_only_in_child_environment(llm_settings, tmp_path, monkeypatch):
    original = tmp_path / "original.docx"
    original.write_bytes(b"synthetic placeholder; mocked worker never reads it")
    secret = llm_settings.yandex_api_key.get_secret_value()

    def worker(command, **kwargs):
        assert secret not in repr(command) and secret not in kwargs["input"]
        assert kwargs["env"]["YANDEX_API_KEY"] == secret
        assert "OLLAMA_API_KEY" not in kwargs["env"] and "VLLM_API_KEY" not in kwargs["env"]
        request = json.loads(kwargs["input"])
        assert "yandex_api_key" not in request["settings"]
        payload = {"findings": [], "llm": LLMAnalysis(status="completed", provider="yandex", json_valid=True, references_valid=True).model_dump()}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("backend.llm.runner.subprocess.run", worker)
    findings, metadata = run_llm_analysis(original, llm_settings, "yandex", allow_cloud=True,
                                          synthetic_data=True, timeout=10)
    assert not findings and metadata.status == "completed"
    assert secret not in metadata.model_dump_json()


def test_wall_clock_timeout_returns_fallback_metadata(llm_settings, tmp_path, monkeypatch):
    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 0.25
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("backend.llm.runner.subprocess.run", timeout)
    findings, metadata = run_llm_analysis(tmp_path / "original.docx", llm_settings, "ollama", timeout=0.25)
    assert not findings and metadata.status == "failed" and metadata.error_code == "llm_timeout"
    assert metadata.attempts is None
