"""Environment configuration without network connections at import time."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Document AI Inspector"
    api_base_url: str = "http://api:8000"
    ollama_base_url: str = "http://host.docker.internal:11434/v1"
    ollama_model: str = ""
    ollama_api_key: SecretStr = SecretStr("")
    vllm_base_url: str = ""
    vllm_model: str = ""
    vllm_api_key: SecretStr = SecretStr("")
    yandex_base_url: str = ""
    yandex_folder_id: str = ""
    yandex_model: str = ""
    yandex_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = Field(default=30, ge=0.1, le=120)
    llm_total_timeout_seconds: float = Field(default=45, ge=0.1, le=300)
    llm_max_retries: int = Field(default=0, ge=0, le=3)
    llm_temperature: float = Field(default=0, ge=0, le=2)
    llm_max_tokens: int = Field(default=1024, ge=128, le=4096)
    llm_max_input_chars: int = Field(default=20000, ge=1024, le=100000)
    llm_max_fragments: int = Field(default=200, ge=2, le=1000)
    llm_max_response_chars: int = Field(default=50000, ge=1024, le=200000)
    llm_json_mode: bool = True
    mlflow_tracking_uri: str = "http://mlflow:5000"
    libreoffice_binary: str = "libreoffice"
    processing_timeout_seconds: int = Field(default=120, ge=1, le=600)
    data_dir: Path = Path("results")
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_zip_entries: int = Field(default=1000, ge=1, le=10000)
    max_uncompressed_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    max_zip_ratio: float = Field(default=200, ge=1, le=1000)
    render_timeout_seconds: int = Field(default=60, ge=1, le=300)
    max_pdf_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    max_pdf_pages: int = Field(default=300, ge=1, le=1000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
