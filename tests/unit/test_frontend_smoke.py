"""UI startup failures must be clear and must never submit a document."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from frontend.client import APIError


def test_api_unavailable_renders_without_exceptions(monkeypatch):
    def offline(*args, **kwargs):
        raise APIError("Нет соединения с API.")
    monkeypatch.setattr("frontend.client.APIClient.options", offline)
    app_path = Path(__file__).resolve().parents[2] / "frontend" / "app.py"
    app = AppTest.from_file(str(app_path)).run(timeout=15)
    assert not app.exception
    assert app.title[0].value == "Document AI Inspector"
    assert "Нет соединения" in app.error[0].value
    assert app.button[0].label == "Повторить подключение"
