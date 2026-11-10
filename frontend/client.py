"""HTTP calls to FastAPI; no local processing, redirects or automatic retries."""

from dataclasses import dataclass, field
from uuid import UUID

import httpx
from pydantic import ValidationError

from backend.core.schemas import DocumentReport, InspectionOptions


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class APIError(Exception):
    """Only application-owned messages may be displayed; never raw exceptions."""


ERROR_MESSAGES = {
    "invalid_docx": "Файл не является корректным DOCX.",
    "invalid_xml": "В DOCX обнаружен повреждённый XML.",
    "file_too_large": "Файл превышает допустимый размер.",
    "unknown_profile": "Профиль проверки недоступен. Обновите страницу.",
    "cloud_consent_required": "Для Yandex подтвердите синтетические данные и платный облачный вызов.",
    "original_changed": "Нарушена неизменность исходного файла.",
    "artifact_changed": "Файл не прошёл проверку целостности; скачивание заблокировано.",
    "processing_timeout": "Истекло время обработки документа.",
}


@dataclass
class InspectionResult:
    report: DocumentReport
    artifacts: dict[str, bytes] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class APIClient:
    def __init__(self, base_url: str, *, timeout: float = 630, http_client=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http_client = http_client

    def _request(self, method, path, *, processing=False, **kwargs):
        try:
            timeout = self.timeout if processing else 10
            if self.http_client is not None:
                response = self.http_client.request(method, self.base_url + path, timeout=timeout, **kwargs)
            else:
                with httpx.Client(follow_redirects=False, trust_env=False) as client:
                    response = client.request(method, self.base_url + path, timeout=timeout, **kwargs)
        except httpx.TimeoutException:
            raise APIError("API не ответил вовремя. Обработка могла продолжиться; автоматического повтора нет.") from None
        except httpx.HTTPError:
            raise APIError("Нет соединения с API. Проверьте, запущен ли сервис api.") from None
        if not 200 <= response.status_code < 300:
            code = None
            try:
                detail = response.json().get("detail")
                if isinstance(detail, dict):
                    code = detail.get("code")
            except (ValueError, AttributeError):
                pass
            fallback = {404: "Отчёт или файл не найден.", 409: "Скачивание заблокировано проверкой целостности.",
                        413: "Файл превышает допустимый размер.", 415: "Загрузите файл DOCX.",
                        422: "API отклонил документ или параметры проверки."}
            raise APIError(ERROR_MESSAGES.get(code, fallback.get(response.status_code, "Ошибка API. Проверьте состояние сервиса.")))
        return response

    @staticmethod
    def _validated(response, schema):
        try:
            return schema.model_validate_json(response.content)
        except (ValidationError, ValueError):
            raise APIError("API вернул ответ, не соответствующий контракту.") from None

    def options(self) -> InspectionOptions:
        return self._validated(self._request("GET", "/documents/options"), InspectionOptions)

    def report(self, job_id: UUID) -> DocumentReport:
        report = self._validated(self._request("GET", f"/documents/{UUID(str(job_id))}/report"), DocumentReport)
        if report.job_id != job_id:
            raise APIError("API вернул отчёт другого задания.")
        return report

    def check(self, filename, data: bytes, *, profile, provider="none", auto_fix=False, render_pdf=False,
              allow_cloud=False, synthetic_data=False) -> InspectionResult:
        if provider == "yandex" and not (allow_cloud and synthetic_data):
            raise APIError(ERROR_MESSAGES["cloud_consent_required"])
        fields = {"profile": profile, "provider": provider, "use_llm": provider != "none",
                  "auto_fix": auto_fix, "render_pdf": render_pdf,
                  "allow_cloud": allow_cloud if provider == "yandex" else False, "synthetic_data": synthetic_data}
        response = self._request("POST", "/documents/check", processing=True,
            files={"file": (filename, data, DOCX_MIME)},
            data={key: str(value).lower() if isinstance(value, bool) else value for key, value in fields.items()})
        report = self._validated(response, DocumentReport)
        result = InspectionResult(report)
        try:
            result.artifacts["report"] = self.report(report.job_id).model_dump_json(indent=2).encode("utf-8")
        except APIError as exc:
            result.errors["report"] = str(exc)
        paths = {"original": f"/documents/{report.job_id}/download?original=true"}
        if report.download_url:
            paths["docx"] = f"/documents/{report.job_id}/download"
        if report.preview.status == "available" and report.preview_url:
            paths["pdf"] = f"/documents/{report.job_id}/preview"
        for key, path in paths.items():
            try:
                result.artifacts[key] = self._request("GET", path).content
            except APIError as exc:
                result.errors[key] = str(exc)
        return result
