"""Environment configuration without network connections at import time."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Document AI Inspector"
    api_base_url: str = "http://api:8000"
    ollama_base_url: str = "http://host.docker.internal:11434/v1"
    ollama_model: str = ""
    mlflow_tracking_uri: str = "http://mlflow:5000"
    libreoffice_binary: str = "libreoffice"
    processing_timeout_seconds: int = Field(default=120, ge=1, le=600)


@lru_cache
def get_settings() -> Settings:
    return Settings()
