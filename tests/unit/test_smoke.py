"""Offline smoke checks for stage 1."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.core.config import Settings
from backend.main import create_app


def test_health_returns_exact_contract():
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"] == "application/json"


def test_configuration_reads_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://local-test:11434/v1")
    monkeypatch.setenv("PROCESSING_TIMEOUT_SECONDS", "42")
    settings = Settings(_env_file=None)
    assert settings.ollama_base_url == "http://local-test:11434/v1"
    assert settings.processing_timeout_seconds == 42


@pytest.mark.parametrize("value", ["0", "601", "not-a-number"])
def test_configuration_rejects_invalid_timeout(monkeypatch, value):
    monkeypatch.setenv("PROCESSING_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_environment_overrides_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_NAME=Имя из файла\nPROCESSING_TIMEOUT_SECONDS=45\n"
        "UNRELATED_SETTING=ignored\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("PROCESSING_TIMEOUT_SECONDS", raising=False)
    from_file = Settings(_env_file=env_file)
    assert from_file.app_name == "Имя из файла"
    assert from_file.processing_timeout_seconds == 45

    monkeypatch.setenv("APP_NAME", "Environment override")
    assert Settings(_env_file=env_file).app_name == "Environment override"


def test_api_uses_configured_application_name(monkeypatch):
    # Avoid changing the process-wide settings cache used by other tests.
    settings = Settings(_env_file=None, app_name="Configured API")
    monkeypatch.setattr("backend.main.get_settings", lambda: settings)
    with TestClient(create_app()) as client:
        assert client.get("/openapi.json").json()["info"]["title"] == "Configured API"
