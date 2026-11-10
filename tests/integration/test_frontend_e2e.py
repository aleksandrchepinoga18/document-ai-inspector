"""Streamlit -> HTTP client -> actual FastAPI -> worker -> stored artifacts.

ASGI transport replaces TCP. Only LibreOffice/LLM are mocked in named tests.
"""

import hashlib
import json
import subprocess
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from backend.core.config import Settings
from backend.main import create_app
from backend.documents.editor import apply_fixes
from backend.documents.parser import parse_document
from backend.llm.analyzer import analyze_document
from backend.pipeline.worker import process_request
from conftest import mock_chat_completion, mock_semantic_payload, rewrite_zip
from frontend.client import APIClient, APIError
from samples.generate import build_document


APP_PATH = Path(__file__).parents[2] / "frontend" / "app.py"


@pytest.fixture
def connected(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path / "jobs", ollama_model="")
    application = create_app(settings)
    calls = []
    @application.middleware("http")
    async def record(request, call_next):
        calls.append((request.method, request.url.path))
        return await call_next(request)
    with TestClient(application) as transport:
        api = APIClient("http://testserver", http_client=transport)
        monkeypatch.setattr("frontend.client.APIClient", lambda *a, **kw: api)
        yield api, settings, calls


def widget(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


def example_app():
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)
    widget(app, "radio", "Источник документа").set_value("Синтетический пример").run(timeout=30)
    return app


def test_options_are_server_profiles_and_limits(connected):
    api, settings, _ = connected
    options = api.options()
    assert [(p.name, p.version) for p in options.profiles] == [("default", "1.0.0")]
    assert options.max_upload_bytes == settings.max_upload_bytes


def test_ui_check_fix_report_and_downloads_use_real_api(connected):
    _, settings, calls = connected
    app = example_app()
    assert not app.exception
    assert not any(method == "POST" for method, _ in calls)
    widget(app, "checkbox", "Применить разрешённые безопасные правки").check().run(timeout=30)
    widget(app, "button", "Проверить документ").click().run(timeout=30)
    assert not app.exception and not app.error
    result = app.session_state["inspection_result"]
    assert result.report.correction.status == "applied"
    assert result.report.correction.validation == "passed"
    assert len(result.report.findings) == 4
    assert sum(f.status == "fixed" for f in result.report.findings) == 1
    assert set(result.artifacts) == {"original", "docx", "report"}
    assert result.artifacts["original"] != result.artifacts["docx"]
    original = (settings.data_dir / str(result.report.job_id) / "original.docx").read_bytes()
    assert result.artifacts["original"] == original
    assert hashlib.sha256(original).hexdigest() == result.report.original_sha256
    assert json.loads(result.artifacts["report"])["job_id"] == str(result.report.job_id)
    assert app.dataframe[0].value["Источник"].tolist() == ["rule"] * 4
    assert any("инварианты пройдены" in item.value for item in app.success)
    assert sum(method == "POST" for method, _ in calls) == 1
    # Ordinary reruns retain the result and never resubmit the upload.
    app.run(timeout=30)
    assert "inspection_result" in app.session_state
    assert sum(method == "POST" for method, _ in calls) == 1
    widget(app, "selectbox", "Пример").set_value("Без ошибок оформления").run(timeout=30)
    assert "inspection_result" not in app.session_state


def test_ui_cloud_is_disabled_until_both_consents_and_resets_on_new_file(connected):
    _, _, calls = connected
    app = example_app()
    widget(app, "selectbox", "Провайдер смысловой проверки").set_value("Yandex AI Studio (облако, платно)").run(timeout=30)
    assert widget(app, "button", "Проверить документ").disabled
    widget(app, "checkbox", "Подтверждаю: документ содержит только синтетические данные").check().run(timeout=30)
    assert widget(app, "button", "Проверить документ").disabled
    widget(app, "checkbox", "Разрешаю платный вызов Yandex для этой проверки").check().run(timeout=30)
    assert not widget(app, "button", "Проверить документ").disabled
    widget(app, "selectbox", "Пример").set_value("Смысловое противоречие").run(timeout=30)
    assert widget(app, "button", "Проверить документ").disabled
    assert not any(method == "POST" for method, _ in calls)


def test_ui_unconfigured_llm_preserves_formal_results_without_network(connected):
    app = example_app()
    widget(app, "selectbox", "Провайдер смысловой проверки").set_value("Ollama (локальная)").run(timeout=30)
    widget(app, "button", "Проверить документ").click().run(timeout=30)
    assert not app.exception
    result = app.session_state["inspection_result"]
    assert result.report.llm.error_code == "llm_not_configured"
    assert len(result.report.findings) == 4 and "docx" in result.artifacts
    assert any("Формальные результаты сохранены" in item.value for item in app.warning)


def test_client_rejects_broken_docx_and_preserves_bytes_when_not_fixing(connected):
    api, _, _ = connected
    with pytest.raises(APIError, match="корректным DOCX"):
        api.check("broken.docx", b"broken", profile="default")
    data = build_document("format_errors")
    result = api.check("upload.docx", data, profile="default")
    assert result.artifacts["original"] == result.artifacts["docx"] == data


def test_ui_downloads_valid_pdf_with_explicitly_mocked_conversion(connected, monkeypatch):
    def convert(command, timeout):
        out = Path(command[command.index("--outdir") + 1])
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), "Test-only conversion fixture")
            (out / "input.pdf").write_bytes(document.tobytes())
        return 0
    monkeypatch.setattr("backend.documents.renderer.run_libreoffice", convert)
    app = example_app()
    widget(app, "checkbox", "Создать PDF-предпросмотр").check().run(timeout=30)
    widget(app, "button", "Проверить документ").click().run(timeout=30)
    assert not app.exception
    result = app.session_state["inspection_result"]
    assert result.report.preview.status == "available"
    with pymupdf.open(stream=result.artifacts["pdf"], filetype="pdf") as pdf:
        assert pdf.page_count == result.report.preview.pages == 1


def test_ui_pdf_failure_keeps_docx_and_report(connected, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr("backend.documents.renderer.run_libreoffice", missing)
    app = example_app()
    widget(app, "checkbox", "Создать PDF-предпросмотр").check().run(timeout=30)
    widget(app, "button", "Проверить документ").click().run(timeout=30)
    assert not app.exception
    result = app.session_state["inspection_result"]
    assert result.report.preview.status == "failed"
    assert "pdf" not in result.artifacts and {"docx", "report"} <= result.artifacts.keys()
    assert any("PDF недоступен" in item.value for item in app.warning)


def test_ui_rejected_correction_never_offers_corrected_download(connected, monkeypatch):
    def corrupt(data, parsed, findings):
        candidate, edits = apply_fixes(data, parsed, findings)
        return rewrite_zip(candidate, {"docProps/core.xml": b"<changed/>"}), edits
    def worker(command, **kwargs):
        result = process_request(json.loads(kwargs["input"]))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result), stderr="")
    monkeypatch.setattr("backend.pipeline.worker.apply_fixes", corrupt)
    monkeypatch.setattr("backend.pipeline.checker.subprocess.run", worker)
    app = example_app()
    widget(app, "checkbox", "Применить разрешённые безопасные правки").check().run(timeout=30)
    widget(app, "button", "Проверить документ").click().run(timeout=30)
    assert not app.exception
    result = app.session_state["inspection_result"]
    assert result.report.correction.status == "rejected"
    assert "docx" not in result.artifacts and {"original", "report"} <= result.artifacts.keys()
    assert any("Правки отклонены" in item.value for item in app.error)


def test_ui_semantic_result_with_explicit_http_mock(connected, monkeypatch, llm_settings):
    import httpx

    def offline_llm(path, settings, provider, **kwargs):
        parsed = parse_document(path.read_bytes())
        from backend.llm.analyzer import extract_fragments
        payload = mock_semantic_payload(extract_fragments(parsed))
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=mock_chat_completion(json.dumps(payload))))
        return analyze_document(parsed, llm_settings, provider, transport=transport)
    monkeypatch.setattr("backend.pipeline.checker.run_llm_analysis", offline_llm)
    app = example_app()
    widget(app, "selectbox", "Пример").set_value("Смысловое противоречие").run(timeout=30)
    widget(app, "selectbox", "Провайдер смысловой проверки").set_value("Ollama (локальная)").run(timeout=30)
    widget(app, "button", "Проверить документ").click().run(timeout=30)
    assert not app.exception
    result = app.session_state["inspection_result"]
    assert result.report.llm.status == "completed"
    assert len(result.report.findings) == 1 and result.report.findings[0].source == "llm"
    assert not result.report.findings[0].auto_fix_allowed
    assert app.dataframe[0].value["Источник"].tolist() == ["llm"]
