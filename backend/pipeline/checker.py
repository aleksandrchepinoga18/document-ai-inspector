"""UUID-scoped, persistent jobs; originals are written once and never edited."""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

from backend.core.config import Settings
from backend.core.schemas import DocumentReport, Preview, RuleProfile
from backend.documents.errors import DocumentError
from backend.documents.renderer import render_pdf as convert_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def check_document(data: bytes, filename: str, profile: RuleProfile, settings: Settings,
                   auto_fix: bool = False, render_pdf: bool = False) -> DocumentReport:
    started = time.monotonic()
    if len(data) > settings.max_upload_bytes:
        raise DocumentError("file_too_large", "Превышен лимит размера DOCX.", 413)
    job_id = uuid4()
    directory = settings.data_dir.resolve() / str(job_id)
    directory.mkdir(parents=True, exist_ok=False)
    original = directory / "original.docx"
    with original.open("xb") as stream:
        stream.write(data)
    with (directory / "profile.json").open("x", encoding="utf-8") as stream:
        stream.write(profile.model_dump_json(indent=2))
    original_hash = hashlib.sha256(data).hexdigest()
    request = {
        "original_path": str(original), "settings": settings.model_dump(mode="json"),
        "profile": profile.model_dump(mode="json"),
        "auto_fix": auto_fix,
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
            auto_fix=auto_fix, render_pdf=render_pdf,
            original_download_url=f"/documents/{job_id}/download?original=true",
        )
        if report.correction.status != "rejected":
            report.download_url = f"/documents/{job_id}/download"
        if render_pdf:
            source = "corrected" if report.correction.status == "applied" else "original"
            try:
                content = (directory / "corrected.docx").read_bytes() if source == "corrected" else data
                expected = report.correction.output_sha256 if source == "corrected" else original_hash
                if hashlib.sha256(content).hexdigest() != expected:
                    raise DocumentError("artifact_changed", "Источник PDF не прошёл проверку SHA-256.", 409)
                remaining = settings.processing_timeout_seconds - (time.monotonic() - started)
                pdf, pages = convert_pdf(content, settings, timeout=remaining)
                with (directory / "preview.pdf").open("xb") as stream:
                    stream.write(pdf)
                report.preview = Preview(
                    status="available", source=source, pages=pages, sha256=hashlib.sha256(pdf).hexdigest(),
                )
                report.preview_url = f"/documents/{job_id}/preview"
            except DocumentError as exc:
                report.preview = Preview(status="failed", source=source, error_code=exc.code, message=exc.message)
            except OSError:
                report.preview = Preview(
                    status="failed", source=source, error_code="render_failed",
                    message="Не удалось сохранить PDF; DOCX и отчёт доступны.",
                )
        if original.read_bytes() != data:
            raise DocumentError("original_changed", "Нарушена неизменность исходного файла.", 500)
        report.metrics["duration_seconds"] = round(time.monotonic() - started, 4)
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


def read_artifact(job_id: UUID, settings: Settings, *, original: bool = False,
                  preview: bool = False) -> tuple[bytes, str]:
    report = read_report(job_id, settings)
    if preview:
        if report.preview.status != "available":
            raise DocumentError("preview_unavailable", "PDF не сформирован; причина указана в отчёте.", 404)
        name, expected = "preview.pdf", report.preview.sha256
    elif original:
        name, expected = "original.docx", report.original_sha256
    elif report.correction.status == "rejected":
        raise DocumentError("unsafe_correction", "Выдача исправленной версии запрещена. Исходник: "
                            + f"/documents/{job_id}/download?original=true", 409)
    elif report.correction.status == "applied":
        if report.correction.validation != "passed":
            raise DocumentError("unsafe_correction", "Инварианты исправленной версии не подтверждены.", 409)
        name, expected = "corrected.docx", report.correction.output_sha256
    else:
        name, expected = "original.docx", report.original_sha256
    path = settings.data_dir.resolve() / str(job_id) / name
    if not path.is_file():
        raise DocumentError("artifact_not_found", "Файл результата не найден.", 404)
    size_limit = settings.max_pdf_bytes if preview else settings.max_upload_bytes
    if path.stat().st_size > size_limit:
        raise DocumentError("artifact_changed", "Размер сохранённого файла превышает допустимый лимит.", 409)
    content = path.read_bytes()
    if not expected or hashlib.sha256(content).hexdigest() != expected:
        raise DocumentError("artifact_changed", "Контрольная сумма файла не совпадает с отчётом; выдача запрещена.", 409)
    return content, name
