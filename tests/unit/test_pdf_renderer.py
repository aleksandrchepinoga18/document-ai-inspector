"""PyMuPDF checks are real; LibreOffice conversion is mocked by default."""

import subprocess
from pathlib import Path

import pymupdf
import pytest

from backend.core.config import Settings
from backend.documents.errors import DocumentError
from backend.documents.renderer import render_pdf, validate_pdf


def pdf_bytes():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Synthetic preview")
        return pdf.tobytes()


def test_pdf_must_open_and_decode():
    assert validate_pdf(pdf_bytes(), Settings(_env_file=None)) == 1
    with pytest.raises(DocumentError):
        validate_pdf(b"%PDF-this-is-not-a-valid-file", Settings(_env_file=None))


def test_pdf_limits():
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        data = pdf.tobytes()
    with pytest.raises(DocumentError):
        validate_pdf(data, Settings(_env_file=None, max_pdf_pages=1))
    with pytest.raises(DocumentError):
        validate_pdf(pdf_bytes() + b" " * 2000, Settings(_env_file=None, max_pdf_bytes=1024))


def test_conversion_uses_unique_profile_and_directories(valid_docx, monkeypatch):
    calls = []

    def convert(command, timeout):
        assert 0 < timeout <= 60
        assert "--headless" in command
        assert command[1].startswith("-env:UserInstallation=file:")
        source = Path(command[-1])
        assert source.read_bytes() == valid_docx
        output = Path(command[command.index("--outdir") + 1])
        assert source.parent != output
        calls.append((source.parent, command[1]))
        (output / "input.pdf").write_bytes(pdf_bytes())
        return 0

    monkeypatch.setattr("backend.documents.renderer.run_libreoffice", convert)
    for _ in range(2):
        data, pages = render_pdf(valid_docx, Settings(_env_file=None))
        assert pages == 1 and data.startswith(b"%PDF-")
    assert calls[0] != calls[1]


@pytest.mark.parametrize("failure,code", [("missing", "libreoffice_unavailable"), ("timeout", "render_timeout"),
                                         ("exit", "render_failed"), ("no_pdf", "render_failed"),
                                         ("invalid_pdf", "invalid_pdf")])
def test_conversion_failure_never_returns_a_fake_pdf(valid_docx, monkeypatch, failure, code):
    def convert(command, timeout):
        if failure == "missing":
            raise FileNotFoundError
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, timeout)
        if failure == "invalid_pdf":
            output = Path(command[command.index("--outdir") + 1])
            (output / "input.pdf").write_bytes(b"not PDF")
        return 1 if failure == "exit" else 0

    monkeypatch.setattr("backend.documents.renderer.run_libreoffice", convert)
    with pytest.raises(DocumentError) as exc:
        render_pdf(valid_docx, Settings(_env_file=None))
    assert exc.value.code == code


def test_fields_are_not_evaluated_by_libreoffice(valid_docx, monkeypatch):
    import io
    from docx import Document
    from docx.oxml import OxmlElement

    document = Document(io.BytesIO(valid_docx))
    field = OxmlElement("w:instrText")
    field.text = 'INCLUDETEXT "https://example.invalid/private"'
    document.paragraphs[1].add_run()._r.append(field)
    data = io.BytesIO()
    document.save(data)

    def must_not_run(*args, **kwargs):
        pytest.fail("A document containing field instructions reached LibreOffice")

    monkeypatch.setattr("backend.documents.renderer.run_libreoffice", must_not_run)
    with pytest.raises(DocumentError) as exc:
        render_pdf(data.getvalue(), Settings(_env_file=None))
    assert exc.value.code == "unsafe_render_content"
