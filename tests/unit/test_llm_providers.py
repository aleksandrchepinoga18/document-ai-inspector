"""Actual SDK over MockTransport: no real model, network or billing."""

import json
import logging

import httpx
import pytest

from backend.documents.parser import parse_document
from backend.llm.analyzer import analyze_document, extract_fragments
from backend.llm.errors import LLMError
from backend.llm.providers import create_provider
from conftest import mock_chat_completion, mock_semantic_payload
from samples.generate import build_document


def sample():
    return parse_document(build_document("semantic_conflict"))


@pytest.mark.parametrize("name", ["ollama", "vllm", "yandex"])
def test_each_provider_uses_configured_endpoint_auth_and_generation(llm_settings, name):
    captured = []
    fragments = extract_fragments(sample())

    def handler(request):
        body = json.loads(request.content)
        captured.append((request, body))
        assert request.method == "POST" and request.url.path == "/v1/chat/completions"
        assert body["temperature"] == llm_settings.llm_temperature
        assert body["max_tokens"] == llm_settings.llm_max_tokens
        assert body["response_format"] == {"type": "json_object"}
        assert "tools" not in body
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"
        return httpx.Response(200, json=mock_chat_completion(json.dumps(mock_semantic_payload(fragments))))

    provider = create_provider(name, llm_settings, transport=httpx.MockTransport(handler),
                               allow_cloud=name == "yandex", synthetic_data=name == "yandex")
    reply = provider.analyze(fragments)
    assert reply.attempts == 1 and len(captured) == 1
    assert captured[0][1]["model"] == provider.model
    assert captured[0][0].headers["authorization"] == "Bearer " + getattr(llm_settings, name + "_api_key").get_secret_value()
    if name == "yandex":
        assert provider.model == "gpt://synthetic-folder/synthetic-model/latest"


@pytest.mark.parametrize("status,code", [(401, "llm_auth_failed"), (403, "llm_auth_failed"),
                                       (404, "llm_model_or_endpoint_not_found"), (429, "llm_quota_or_rate_limit"),
                                       (400, "llm_request_rejected"), (503, "llm_unavailable")])
def test_http_failures_are_explicit_and_sanitized(llm_settings, status, code, caplog):
    secret = llm_settings.yandex_api_key.get_secret_value()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": secret, "code": "synthetic_error"}})

    with caplog.at_level(logging.INFO):
        findings, metadata = analyze_document(sample(), llm_settings, "yandex", allow_cloud=True,
                                              synthetic_data=True, transport=httpx.MockTransport(handler))
    assert findings == [] and metadata.status == "failed" and metadata.error_code == code
    assert metadata.attempts == 1 and len(calls) == 1
    assert secret not in metadata.model_dump_json() and secret not in caplog.text
    assert secret not in repr(llm_settings)


@pytest.mark.parametrize("failure,code", [("timeout", "llm_timeout"), ("connection", "llm_unavailable")])
def test_transport_failures(llm_settings, failure, code):
    def handler(request):
        error = httpx.ReadTimeout if failure == "timeout" else httpx.ConnectError
        raise error("synthetic private exception details", request=request)

    findings, metadata = analyze_document(sample(), llm_settings, "ollama", transport=httpx.MockTransport(handler))
    assert not findings and metadata.error_code == code
    assert "private exception" not in metadata.message


def test_retries_are_bounded_and_sdk_retries_disabled(llm_settings):
    llm_settings.llm_max_retries = 1
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "temporarily unavailable"}})
        return httpx.Response(200, json=mock_chat_completion('{"issues":[]}'))

    findings, metadata = analyze_document(sample(), llm_settings, "vllm", transport=httpx.MockTransport(handler))
    assert not findings and metadata.status == "completed"
    assert metadata.attempts == 2 and len(calls) == 2


def test_quota_failure_is_never_retried(llm_settings):
    llm_settings.llm_max_retries = 3
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "quota exceeded"}})

    _, metadata = analyze_document(sample(), llm_settings, "yandex", allow_cloud=True,
                                   synthetic_data=True, transport=httpx.MockTransport(handler))
    assert metadata.error_code == "llm_quota_or_rate_limit" and len(calls) == 1


def test_retry_after_cannot_extend_or_bypass_the_deadline(llm_settings):
    llm_settings.llm_max_retries = 2
    llm_settings.llm_total_timeout_seconds = 1
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, headers={"Retry-After": "60"}, json={"error": {"message": "busy"}})

    _, metadata = analyze_document(sample(), llm_settings, "ollama", transport=httpx.MockTransport(handler))
    assert metadata.status == "failed" and len(calls) == 1


@pytest.mark.parametrize("allow,synthetic", [(False, False), (True, False), (False, True)])
def test_yandex_requires_both_per_call_confirmations(llm_settings, allow, synthetic):
    with pytest.raises(LLMError) as exc:
        create_provider("yandex", llm_settings, allow_cloud=allow, synthetic_data=synthetic)
    assert exc.value.code == "cloud_consent_required"


def test_empty_model_blocks_calls_without_blocking_app_configuration(llm_settings):
    llm_settings.ollama_model = ""
    findings, metadata = analyze_document(sample(), llm_settings, "ollama")
    assert not findings and metadata.error_code == "llm_not_configured" and metadata.attempts == 0


def test_cloud_model_and_public_local_provider_urls_are_blocked(llm_settings):
    llm_settings.ollama_model = "synthetic:cloud"
    with pytest.raises(LLMError, match="локальная"):
        create_provider("ollama", llm_settings)
    llm_settings.ollama_model = "synthetic-local"
    llm_settings.ollama_base_url = "https://example.com/v1"
    with pytest.raises(LLMError) as exc:
        create_provider("ollama", llm_settings)
    assert exc.value.code == "remote_endpoint_blocked"


def test_json_mode_can_be_disabled_without_disabling_validation(llm_settings):
    llm_settings.llm_json_mode = False

    def handler(request):
        assert "response_format" not in json.loads(request.content)
        return httpx.Response(200, json=mock_chat_completion("not JSON"))

    _, metadata = analyze_document(sample(), llm_settings, "ollama", transport=httpx.MockTransport(handler))
    assert metadata.error_code == "invalid_llm_json" and metadata.json_valid is False


def test_truncated_response_is_not_accepted(llm_settings):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_chat_completion('{"issues":[]}', finish_reason="length")))
    findings, metadata = analyze_document(sample(), llm_settings, "ollama", transport=transport)
    assert not findings and metadata.error_code == "incomplete_llm_response"


@pytest.mark.parametrize("escaped", [False, True])
def test_reflected_credentials_cannot_enter_a_saved_finding(llm_settings, escaped):
    fragments = extract_fragments(sample())
    payload = mock_semantic_payload(fragments)
    secret = llm_settings.yandex_api_key.get_secret_value()
    payload["issues"][0]["explanation"] += secret
    content = json.dumps(payload)
    if escaped:
        content = content.replace(secret, "".join("\\u%04x" % ord(c) for c in secret))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=mock_chat_completion(content)))
    findings, metadata = analyze_document(sample(), llm_settings, "yandex", allow_cloud=True,
                                          synthetic_data=True, transport=transport)
    assert not findings and metadata.error_code == "llm_sensitive_response"
    assert secret not in metadata.model_dump_json()
