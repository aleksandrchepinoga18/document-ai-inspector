"""OpenAI-compatible adapters, explicit configuration and bounded retry behavior."""

import ipaddress
import math
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError
from pydantic import SecretStr

from backend.core.config import Settings
from backend.llm.errors import LLMError
from backend.llm.prompts import messages
from backend.llm.schemas import Fragment


SECRET_FIELDS = {"ollama_api_key", "vllm_api_key", "yandex_api_key"}


@dataclass
class ProviderReply:
    content: str
    attempts: int


class LLMProvider(ABC):
    @abstractmethod
    def analyze(self, fragments: list[Fragment]) -> ProviderReply:
        """Return model text for subsequent independent schema/evidence validation."""


def local_host(hostname: str) -> bool:
    if hostname in {"localhost", "host.docker.internal"} or hostname.endswith((".localhost", ".internal")):
        return True
    try:
        address = ipaddress.ip_address(hostname)
        return address.is_loopback or address.is_private
    except ValueError:
        return "." not in hostname and ":" not in hostname


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


class CompatibleProvider(LLMProvider):
    name: str

    def __init__(self, settings: Settings, *, transport=None, allow_cloud=False, synthetic_data=False):
        self.settings = settings
        if self.name == "yandex" and not (allow_cloud and synthetic_data):
            raise LLMError("cloud_consent_required", "Yandex требует явных allow_cloud=true и synthetic_data=true.")
        self.base_url = getattr(settings, self.name + "_base_url").strip()
        self.model = getattr(settings, self.name + "_model").strip()
        self.key: SecretStr = getattr(settings, self.name + "_api_key")
        if self.name == "yandex":
            if not settings.yandex_folder_id.strip() or not self.key.get_secret_value().strip():
                raise LLMError("llm_not_configured", "Настройте YANDEX_FOLDER_ID и YANDEX_API_KEY в окружении.")
            self.model = self.model.replace("{folder_id}", settings.yandex_folder_id.strip())
            if self.model and not self.model.startswith(f"gpt://{settings.yandex_folder_id.strip()}/"):
                raise LLMError("llm_not_configured", "YANDEX_MODEL должен содержать URI модели из выбранного каталога.")
        if not self.base_url or not self.model:
            raise LLMError("llm_not_configured", "Задайте base URL и модель выбранного провайдера в окружении.")
        try:
            url = urlsplit(self.base_url)
            _ = url.port
            if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                    or url.query or url.fragment):
                raise ValueError
        except ValueError as exc:
            raise LLMError("invalid_llm_url", "Некорректный base URL провайдера.") from exc
        if self.name == "yandex" and url.scheme != "https":
            raise LLMError("invalid_llm_url", "Для Yandex требуется HTTPS.")
        if self.name != "yandex" and not local_host(url.hostname):
            raise LLMError("remote_endpoint_blocked", "Ollama/vLLM допускают только настроенный локальный или внутренний адрес.")
        if self.name == "ollama" and (":cloud" in self.model.lower() or self.model.lower().endswith("-cloud")):
            raise LLMError("cloud_model_blocked", "Для Ollama разрешена только локальная модель, без cloud-суффикса.")
        self.transport = transport

    def analyze(self, fragments: list[Fragment]) -> ProviderReply:
        deadline = time.monotonic() + self.settings.llm_total_timeout_seconds
        http_client = httpx.Client(
            timeout=self.settings.llm_timeout_seconds, transport=self.transport,
            follow_redirects=False, trust_env=False,
        )
        # Explicit base URL/key prevent accidental fallback to OPENAI_* environment variables.
        with OpenAI(
            base_url=self.base_url, api_key=self.key.get_secret_value() or "local-no-auth",
            max_retries=0, timeout=self.settings.llm_timeout_seconds, http_client=http_client,
        ) as client:
            for attempt in range(1, self.settings.llm_max_retries + 2):
                delay = 0.25 * 2 ** (attempt - 1)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMError("llm_timeout", "Истекло время LLM-анализа.", attempts=attempt - 1)
                try:
                    options = {"response_format": {"type": "json_object"}} if self.settings.llm_json_mode else {}
                    response = client.chat.completions.create(
                        model=self.model, messages=messages(fragments),
                        temperature=self.settings.llm_temperature, max_tokens=self.settings.llm_max_tokens,
                        timeout=min(self.settings.llm_timeout_seconds, remaining), **options,
                    )
                    if not response.choices:
                        raise LLMError("empty_llm_response", "Модель вернула пустой ответ.", attempts=attempt)
                    choice = response.choices[0]
                    if (choice.finish_reason != "stop" or choice.message.tool_calls or choice.message.refusal
                            or getattr(choice.message, "function_call", None)):
                        raise LLMError("incomplete_llm_response", "Ответ модели обрезан, отклонён или содержит вызов инструмента.", attempts=attempt)
                    content = choice.message.content
                    if not isinstance(content, str) or not content.strip():
                        raise LLMError("empty_llm_response", "Модель не вернула текстовый JSON.", attempts=attempt)
                    if len(content) > self.settings.llm_max_response_chars:
                        raise LLMError("llm_response_too_large", "Ответ модели превышает лимит размера.", attempts=attempt)
                    return ProviderReply(content, attempt)
                except APITimeoutError:
                    error = LLMError("llm_timeout", "Провайдер не ответил за отведённое время.", attempts=attempt)
                    retry = True
                except APIConnectionError:
                    error = LLMError("llm_unavailable", "Не удалось подключиться к выбранному LLM-провайдеру.", attempts=attempt)
                    retry = True
                except APIStatusError as exc:
                    if exc.status_code in {401, 403}:
                        code, message = "llm_auth_failed", "Ошибка авторизации или прав доступа к модели."
                    elif exc.status_code == 429:
                        code, message = "llm_quota_or_rate_limit", "Исчерпана квота или превышен лимит запросов провайдера."
                    elif exc.status_code == 404:
                        code, message = "llm_model_or_endpoint_not_found", "Не найдена модель или совместимый endpoint."
                    elif exc.status_code >= 500:
                        code, message = "llm_unavailable", "Ошибка на стороне LLM-провайдера."
                    else:
                        code, message = "llm_request_rejected", "Провайдер отклонил параметры запроса; проверьте модель и JSON mode."
                    error = LLMError(code, message, attempts=attempt)
                    retry = exc.status_code >= 500
                    delay = max(delay, retry_after_seconds(exc.response.headers.get("retry-after")))
                if not retry or attempt > self.settings.llm_max_retries:
                    raise error
                if delay >= deadline - time.monotonic():
                    raise error
                time.sleep(delay)
        raise LLMError("llm_unavailable", "LLM-анализ не завершён.")


class OllamaProvider(CompatibleProvider):
    name = "ollama"


class VLLMProvider(CompatibleProvider):
    name = "vllm"


class YandexProvider(CompatibleProvider):
    name = "yandex"


def create_provider(name: str, settings: Settings, **kwargs) -> LLMProvider:
    classes = {"ollama": OllamaProvider, "vllm": VLLMProvider, "yandex": YandexProvider}
    if name not in classes:
        raise LLMError("unknown_provider", "Неизвестный LLM-провайдер.")
    return classes[name](settings, **kwargs)
