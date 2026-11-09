"""Environment configuration without network connections at import time."""

from functools import lru_cache
from pathlib import Path

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
    data_dir: Path = Path("results")
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_zip_entries: int = Field(default=1000, ge=1, le=10000)
    max_uncompressed_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    max_zip_ratio: float = Field(default=200, ge=1, le=1000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
