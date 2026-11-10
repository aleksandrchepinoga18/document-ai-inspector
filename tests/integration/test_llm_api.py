"""API, actual deterministic worker and mocked SDK transports in the LLM phase."""

import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.core.schemas import LLMAnalysis
from backend.documents.parser import parse_document
from backend.documents.security import DOCX_MIME
from backend.llm.analyzer import analyze_document, extract_fragments
from backend.main import create_app
from conftest import mock_chat_completion, mock_semantic_payload
from samples.generate import build_document


@pytest.fixture
def client(llm_settings, tmp_path):
    llm_settings.data_dir = tmp_path / "jobs"
    with TestClient(create_app(llm_settings)) as session:
        yield session


def upload(client, data, **flags):
    return client.post("/documents/check", files={"file": ("synthetic.docx", data, DOCX_MIME)}, data=flags)


def patch_analysis(monkeypatch, *, http_status=200, invalid_quote=False):
    def analyze(original, settings, provider, *, allow_cloud, synthetic_data, timeout):
        parsed = parse_document(original.read_bytes())
        fragments = extract_fragments(parsed)
        payload = mock_semantic_payload(fragments)
        if invalid_quote:
            payload["issues"][0]["references"][0]["quote"] = "Nonexistent invented quotation."

        def handler(request):
            if http_status != 200:
                return httpx.Response(http_status, json={"error": {"message": "synthetic provider failure"}})
            return httpx.Response(200, json=mock_chat_completion(json.dumps(payload)))

        return analyze_document(parsed, settings, provider, allow_cloud=allow_cloud,
                                synthetic_data=synthetic_data, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("backend.pipeline.checker.run_llm_analysis", analyze)


@pytest.mark.parametrize("provider", ["ollama", "vllm", "yandex"])
def test_each_selected_provider_publishes_grounded_advisory_findings(client, llm_settings, monkeypatch, provider):
    patch_analysis(monkeypatch)
    data = build_document("semantic_conflict")
    flags = {"use_llm": "true", "provider": provider}
    if provider == "yandex":
        flags.update(allow_cloud="true", synthetic_data="true")
    response = upload(client, data, **flags)
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["llm"]["status"] == "completed"
    assert report["llm"]["json_valid"] and report["llm"]["references_valid"]
    assert report["metrics"]["rule_finding_count"] == 0 and report["metrics"]["llm_finding_count"] == 1
    assert report["findings"][0]["source"] == "llm" and not report["findings"][0]["auto_fix_allowed"]
    assert client.get(report["original_download_url"]).content == data
    assert client.get(report["report_url"]).json() == report
    saved = llm_settings.data_dir / report["job_id"]
    assert (saved / "original.docx").read_bytes() == data
    assert report["original_sha256"] == hashlib.sha256(data).hexdigest()
    assert llm_settings.yandex_api_key.get_secret_value() not in (saved / "report.json").read_text(encoding="utf-8")


def test_disabled_llm_does_not_construct_or_call_any_provider(client, monkeypatch):
    def must_not_call(*args, **kwargs):
        pytest.fail("use_llm=false must not execute the LLM phase")

    monkeypatch.setattr("backend.pipeline.checker.run_llm_analysis", must_not_call)
    report = upload(client, build_document("format_errors"), use_llm="false").json()
    assert report["llm"]["status"] == "not_requested" and len(report["findings"]) == 4


def test_llm_cannot_edit_facts_even_with_auto_fix_enabled(client, monkeypatch):
    patch_analysis(monkeypatch)
    data = build_document("semantic_conflict")
    response = upload(client, data, use_llm="true", provider="ollama", auto_fix="true")
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["correction"]["status"] == "unchanged" and report["correction"]["edits"] == []
    assert report["findings"][0]["source"] == "llm" and report["findings"][0]["status"] == "open"
    assert client.get(report["download_url"]).content == data


@pytest.mark.parametrize("flags", [{}, {"allow_cloud": "true"}, {"synthetic_data": "true"}])
def test_cloud_consent_is_required_before_the_pipeline(client, monkeypatch, flags):
    def must_not_call(*args, **kwargs):
        pytest.fail("Missing cloud consent must not enter the pipeline")

    monkeypatch.setattr("backend.api.documents.check_document", must_not_call)
    response = upload(client, build_document("semantic_conflict"), use_llm="true", provider="yandex", **flags)
    assert response.status_code == 403 and response.json()["detail"]["code"] == "cloud_consent_required"


def test_llm_outage_preserves_rules_and_safe_correction(client, monkeypatch):
    patch_analysis(monkeypatch, http_status=503)
    response = upload(client, build_document("format_errors"), use_llm="true", provider="ollama", auto_fix="true")
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["llm"]["status"] == "failed" and report["llm"]["error_code"] == "llm_unavailable"
    assert report["correction"]["status"] == "applied" and report["metrics"]["fixed_count"] == 1
    assert len(report["findings"]) == 4 and all(f["source"] == "rule" for f in report["findings"])
    assert client.get(report["download_url"]).status_code == 200


def test_ungrounded_response_is_rejected_without_losing_rule_results(client, monkeypatch):
    patch_analysis(monkeypatch, invalid_quote=True)
    response = upload(client, build_document("semantic_conflict"), use_llm="true", provider="vllm")
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["llm"]["status"] == "failed" and report["llm"]["error_code"] == "invalid_llm_evidence"
    assert report["findings"] == []
    assert client.get(report["original_download_url"]).status_code == 200


def test_unexpected_llm_failure_cannot_abort_completed_formal_check(client, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("private SDK details must not escape")

    monkeypatch.setattr("backend.pipeline.checker.run_llm_analysis", crash)
    response = upload(client, build_document("format_errors"), use_llm="true", provider="ollama")
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["llm"]["status"] == "failed" and len(report["findings"]) == 4
    assert "private SDK" not in response.text


def test_missing_configuration_is_a_fallback_not_a_failed_job(client, llm_settings):
    llm_settings.ollama_model = ""
    response = upload(client, build_document("format_errors"), use_llm="true", provider="ollama")
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["llm"]["error_code"] == "llm_not_configured" and len(report["findings"]) == 4
