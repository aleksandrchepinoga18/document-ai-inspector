"""UUID-scoped, persistent jobs; originals are written once and never edited."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

from backend.core.config import Settings
from backend.core.schemas import DocumentReport, RuleProfile
from backend.documents.errors import DocumentError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def check_document(data: bytes, filename: str, profile: RuleProfile, settings: Settings) -> DocumentReport:
    if len(data) > settings.max_upload_bytes:
        raise DocumentError("file_too_large", "Превышен лимит размера DOCX.", 413)
    job_id = uuid4()
    directory = settings.data_dir.resolve() / str(job_id)
    directory.mkdir(parents=True, exist_ok=False)
    original = directory / "original.docx"
    with original.open("xb") as stream:
        stream.write(data)
    original_hash = hashlib.sha256(data).hexdigest()
    request = {
        "original_path": str(original), "settings": settings.model_dump(mode="json"),
        "profile": profile.model_dump(mode="json"),
    }
    try:
        # subprocess.run kills and reaps only its own worker on timeout.
        completed = subprocess.run(
            [sys.executable, "-m", "backend.pipeline.worker"],
            input=json.dumps(request, ensure_ascii=True), capture_output=True,
            text=True, encoding="utf-8", cwd=PROJECT_ROOT,
            timeout=settings.processing_timeout_seconds, check=False,
        )
        if completed.returncode != 0:
            raise DocumentError("worker_failed", "Процесс проверки завершился с ошибкой.", 500)
        result = json.loads(completed.stdout)
        if "error" in result:
            raise DocumentError(**result["error"])
        if original.read_bytes() != data or result["original_sha256"] != original_hash:
            raise DocumentError("original_changed", "Нарушена неизменность исходного файла.", 500)
        report = DocumentReport(
            job_id=job_id, filename=filename, profile=profile.name,
            profile_version=profile.version, report_url=f"/documents/{job_id}/report",
            **result,
        )
        with (directory / "report.json").open("x", encoding="utf-8") as stream:
            stream.write(report.model_dump_json(indent=2))
        return report
    except subprocess.TimeoutExpired as exc:
        error = DocumentError("processing_timeout", "Превышено время проверки DOCX.", 504)
        save_failure(directory, error)
        raise error from exc
    except DocumentError as exc:
        save_failure(directory, exc)
        raise


def save_failure(directory: Path, error: DocumentError) -> None:
    with (directory / "failure.json").open("x", encoding="utf-8") as stream:
        json.dump({"code": error.code, "message": error.message}, stream, ensure_ascii=False)


def read_report(job_id: UUID, settings: Settings) -> DocumentReport:
    path = settings.data_dir.resolve() / str(job_id) / "report.json"
    if not path.is_file():
        raise DocumentError("report_not_found", "Готовый отчёт с таким UUID не найден.", 404)
    try:
        report = DocumentReport.model_validate_json(path.read_text(encoding="utf-8"))
        if report.job_id != job_id:
            raise ValueError("Job identifier mismatch")
        return report
    except ValueError as exc:
        raise DocumentError("invalid_saved_report", "Сохранённый отчёт повреждён.", 500) from exc
