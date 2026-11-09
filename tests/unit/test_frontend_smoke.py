"""Run the stage 1 placeholder without a browser or external services."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_placeholder_renders_without_errors():
    app_path = Path(__file__).resolve().parents[2] / "frontend" / "app.py"
    app = AppTest.from_file(str(app_path)).run(timeout=15)
    assert not app.exception
    assert app.title[0].value == "Document AI Inspector"
    assert "Этап 1" in app.info[0].value
