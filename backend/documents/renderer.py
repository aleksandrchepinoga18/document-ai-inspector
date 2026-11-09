"""Isolated LibreOffice conversion and bounded PyMuPDF preview checks."""

import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pymupdf
from lxml import etree

from backend.core.config import Settings
from backend.documents.errors import DocumentError
from backend.documents.parser import parse_document
from backend.documents.security import safe_xml, validate_docx


def validate_pdf(data: bytes, settings: Settings, deadline: float | None = None) -> int:
    if not data.startswith(b"%PDF-") or len(data) > settings.max_pdf_bytes:
        raise DocumentError("invalid_pdf", "PDF отсутствует, повреждён или превышает лимит.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass or pdf.is_repaired or not 1 <= pdf.page_count <= settings.max_pdf_pages:
                raise DocumentError("invalid_pdf", "PDF зашифрован, повреждён либо имеет недопустимое число страниц.")
            for page in pdf:
                if deadline is not None and time.monotonic() >= deadline:
                    raise DocumentError("render_timeout", "Превышено время проверки PDF.", 504)
                width, height = page.rect.width, page.rect.height
                if not 0 < width <= 20000 or not 0 < height <= 20000:
                    raise DocumentError("invalid_pdf", "Недопустимый размер страницы PDF.")
                # Actually decode each page, keeping the raster dimension <= 128 pixels.
                scale = min(1.0, 128 / max(width, height))
                page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            return pdf.page_count
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("invalid_pdf", "Не удалось открыть или декодировать PDF.") from exc


def reject_active_content(data: bytes, settings: Settings) -> None:
    validate_docx(data, settings)
    parsed = parse_document(data)
    # Field instructions can contain DDE/INCLUDETEXT without an external relationship.
    forbidden = {"fldChar", "fldSimple", "instrText", "object", "altChunk"}
    for part in parsed.document.part.package.parts:
        name = str(part.partname)
        if "/embeddings/" in name or part.content_type == "image/svg+xml":
            raise DocumentError("unsafe_render_content", "PDF для embedded/OLE-объектов и SVG пока не поддерживается.")
        if part.content_type.endswith(("+xml", "/xml")):
            root = safe_xml(part.blob)
            if any(isinstance(node.tag, str) and etree.QName(node).localname in forbidden for node in root.iter()):
                raise DocumentError("unsafe_render_content", "PDF для полей, OLE и альтернативного содержимого пока не поддерживается.")


def run_libreoffice(command: list[str], timeout: float) -> int:
    options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NO_WINDOW}
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options,
    )
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait()
        raise


def render_pdf(data: bytes, settings: Settings, timeout: float | None = None) -> tuple[bytes, int]:
    budget = min(settings.render_timeout_seconds, timeout) if timeout is not None else settings.render_timeout_seconds
    if budget <= 0:
        raise DocumentError("render_timeout", "Истекло время обработки; PDF не сформирован.", 504)
    deadline = time.monotonic() + budget
    reject_active_content(data, settings)
    try:
        with tempfile.TemporaryDirectory(prefix="inspector-render-") as temporary:
            workspace = Path(temporary)
            source = workspace / "input.docx"
            source.write_bytes(data)
            output = workspace / "output"
            output.mkdir()
            profile = workspace / "profile"
            (profile / "user").mkdir(parents=True)
            (profile / "user" / "registrymodifications.xcu").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
                '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
                '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
                '</item></oor:items>', encoding="utf-8",
            )
            command = [
                settings.libreoffice_binary, "-env:UserInstallation=" + profile.as_uri(),
                "--headless", "--norestore", "--nodefault", "--nolockcheck", "--nologo",
                "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(output), str(source),
            ]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DocumentError("render_timeout", "Превышено время подготовки PDF.", 504)
            code = run_libreoffice(command, remaining)
            pdf_path = output / "input.pdf"
            if code != 0 or not pdf_path.is_file():
                raise DocumentError("render_failed", "LibreOffice не сформировал PDF.")
            if pdf_path.stat().st_size > settings.max_pdf_bytes:
                raise DocumentError("invalid_pdf", "Превышен лимит размера PDF.")
            pdf = pdf_path.read_bytes()
            pages = validate_pdf(pdf, settings, deadline)
            return pdf, pages
    except FileNotFoundError as exc:
        raise DocumentError("libreoffice_unavailable", "LibreOffice не найден; DOCX и отчёт остаются доступны.") from exc
    except subprocess.TimeoutExpired as exc:
        raise DocumentError("render_timeout", "Превышено время конвертации LibreOffice.", 504) from exc
    except OSError as exc:
        raise DocumentError("render_failed", "Не удалось выполнить конвертацию PDF.") from exc
