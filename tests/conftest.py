"""In-memory synthetic documents: no fixtures containing user data."""

import io
import zipfile

import pytest

from samples.generate import build_document


@pytest.fixture
def valid_docx():
    return build_document("valid")


@pytest.fixture
def llm_settings():
    from backend.core.config import Settings
    return Settings(
        _env_file=None, ollama_base_url="http://127.0.0.1:11434/v1", ollama_model="synthetic-local",
        ollama_api_key="fake-local-key", vllm_base_url="http://127.0.0.1:8001/v1",
        vllm_model="synthetic-vllm", vllm_api_key="fake-vllm-key",
        yandex_base_url="https://ai.api.cloud.yandex.net/v1", yandex_folder_id="synthetic-folder",
        yandex_model="gpt://{folder_id}/synthetic-model/latest", yandex_api_key="fake-cloud-key-not-real",
        llm_max_retries=0,
    )


def mock_semantic_payload(fragments):
    facts = [f for f in fragments if "Срок исполнения договора" in f.text]
    return {"issues": [{
        "error_type": "contradiction", "severity": "warning",
        "references": [{"element_id": f.element_id, "quote": f.text} for f in facts],
        "explanation": "В разных разделах для одного договора указаны разные сроки исполнения.",
        "suggestion": "Уточните срок исполнения договора вручную.",
    }]}


def mock_chat_completion(content, *, model="synthetic-model", finish_reason="stop"):
    return {
        "id": "chatcmpl-offline-test", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": content}}],
    }


def rewrite_zip(data: bytes, replacements: dict[str, bytes], omit=()) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED,
    ) as target:
        for item in source.infolist():
            if item.filename not in omit and item.filename not in replacements:
                target.writestr(item.filename, source.read(item.filename))
        for name, payload in replacements.items():
            # ZipInfo normalizes OS separators on Windows. Preserve intentionally
            # malicious fixture names so the security tests exercise the same ZIP
            # bytes on both Windows and Linux.
            info = zipfile.ZipInfo(name)
            info.filename = info.orig_filename = name
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, payload)
    return output.getvalue()
