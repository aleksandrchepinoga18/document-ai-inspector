"""Real calls are opt-in, synthetic-only and excluded by ordinary pytest."""

import os

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.documents.security import DOCX_MIME
from backend.main import create_app
from samples.generate import build_document


@pytest.mark.external
def test_real_ollama(tmp_path):
    if os.environ.get("RUN_OLLAMA_TESTS") != "1":
        pytest.skip("Explicit RUN_OLLAMA_TESTS=1 is required")
    run_real_test("ollama", tmp_path)


@pytest.mark.external
def test_real_yandex(tmp_path):
    if os.environ.get("RUN_YANDEX_TESTS") != "1" or os.environ.get("ALLOW_PAID_TESTS") != "1":
        pytest.skip("Explicit RUN_YANDEX_TESTS=1 and ALLOW_PAID_TESTS=1 are required")
    run_real_test("yandex", tmp_path)


def run_real_test(provider, tmp_path):
    settings = Settings(data_dir=tmp_path)
    data = build_document("semantic_conflict")
    flags = {"use_llm": "true", "provider": provider, "synthetic_data": "true"}
    if provider == "yandex":
        flags["allow_cloud"] = "true"
    with TestClient(create_app(settings)) as client:
        response = client.post("/documents/check", files={"file": ("synthetic.docx", data, DOCX_MIME)}, data=flags)
        assert response.status_code == 201
        report = response.json()
        assert report["llm"]["status"] == "completed", report["llm"]
        assert report["llm"]["json_valid"] and report["llm"]["references_valid"]
        assert client.get(report["original_download_url"]).content == data
        assert all(f["source"] == "llm" and not f["auto_fix_allowed"] for f in report["findings"])
        # Zero findings can be a valid model response; this is not an accuracy benchmark.
