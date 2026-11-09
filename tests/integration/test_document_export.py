"""API -> safe correction -> validation -> DOCX/PDF export and failure veto."""

import hashlib
import json
import subprocess
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.documents.editor import apply_fixes
from backend.documents.parser import parse_document
from backend.documents.profiles import load_profile
from backend.documents.rules import inspect_document
from backend.documents.security import DOCX_MIME
from backend.main import create_app
from backend.pipeline.worker import process_request
from conftest import rewrite_zip
from samples.generate import build_document


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "jobs")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as session:
        yield session


def submit(client, data=None, **flags):
    return client.post("/documents/check", files={
        "file": ("synthetic.docx", data or build_document("format_errors"), DOCX_MIME),
    }, data={"auto_fix": "true", **flags})


def fake_conversion(command, timeout):
    output = Path(command[command.index("--outdir") + 1])
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Synthetic conversion fixture; not a real LibreOffice result")
        (output / "input.pdf").write_bytes(pdf.tobytes())
    return 0


def test_corrected_download_original_and_persistence(client, settings):
    original = build_document("format_errors")
    response = submit(client, original)
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["correction"]["status"] == "applied"
    assert report["correction"]["validation"] == "passed"
    assert len(report["correction"]["edits"]) == 1
    assert report["metrics"]["fixed_count"] == 1
    assert report["metrics"]["open_count"] == 3
    assert sum(f["status"] == "fixed" for f in report["findings"]) == 1
    downloaded = client.get(report["download_url"])
    assert downloaded.status_code == 200 and downloaded.headers["content-type"] == DOCX_MIME
    assert hashlib.sha256(downloaded.content).hexdigest() == report["correction"]["output_sha256"]
    remaining = inspect_document(parse_document(downloaded.content), load_profile("default"))
    assert len(remaining) == 3 and not any(f.rule_id == "font_size_pt" for f in remaining)
    assert client.get(report["original_download_url"]).content == original
    assert client.get(f'/documents/{report["job_id"]}/preview').status_code == 404
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(report["download_url"]).content == downloaded.content


def test_pdf_preview_with_real_pdf_validation_and_mock_conversion(client, monkeypatch):
    monkeypatch.setattr("backend.documents.renderer.run_libreoffice", fake_conversion)
    response = submit(client, render_pdf="true")
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["preview"]["status"] == "available"
    assert report["preview"]["source"] == "corrected"
    pdf_response = client.get(report["preview_url"])
    assert pdf_response.status_code == 200 and pdf_response.headers["content-type"] == "application/pdf"
    with pymupdf.open(stream=pdf_response.content, filetype="pdf") as pdf:
        assert pdf.page_count == report["preview"]["pages"] == 1


def test_render_failure_preserves_corrected_docx(client, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("backend.documents.renderer.run_libreoffice", missing)
    response = submit(client, render_pdf="true")
    report = response.json()
    assert report["correction"]["status"] == "applied"
    assert report["preview"]["status"] == "failed"
    assert report["preview"]["error_code"] == "libreoffice_unavailable"
    assert report["preview_url"] is None
    assert client.get(report["download_url"]).status_code == 200


def test_no_edits_or_check_only_returns_original(client, valid_docx):
    for flags in ({"auto_fix": "true"}, {"auto_fix": "false"}):
        report = submit(client, valid_docx, **flags).json()
        expected = "unchanged" if flags["auto_fix"] == "true" else "not_requested"
        assert report["correction"]["status"] == expected
        assert client.get(report["download_url"]).content == valid_docx


def test_unsafe_correction_is_not_published_and_original_is_available(client, settings, monkeypatch):
    def corrupt(data, parsed, findings):
        candidate, edits = apply_fixes(data, parsed, findings)
        return rewrite_zip(candidate, {"docProps/core.xml": b"<changed/>"}), edits

    # Run the same worker in-process only for fault injection across its process boundary.
    def injected_worker(command, **kwargs):
        result = process_request(json.loads(kwargs["input"]))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result), stderr="")

    monkeypatch.setattr("backend.pipeline.worker.apply_fixes", corrupt)
    monkeypatch.setattr("backend.pipeline.checker.subprocess.run", injected_worker)
    data = build_document("format_errors")
    response = submit(client, data)
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["correction"]["status"] == "rejected"
    assert report["correction"]["validation"] == "failed"
    assert "protected_parts_changed" in report["correction"]["errors"]
    assert report["download_url"] is None
    assert client.get(f'/documents/{report["job_id"]}/download').status_code == 409
    assert client.get(report["original_download_url"]).content == data
    assert not (settings.data_dir / report["job_id"] / "corrected.docx").exists()
    assert not any(f["status"] == "fixed" for f in report["findings"])


def test_tampered_saved_artifacts_are_not_served(client, settings, monkeypatch):
    monkeypatch.setattr("backend.documents.renderer.run_libreoffice", fake_conversion)
    report = submit(client, render_pdf="true").json()
    job = settings.data_dir / report["job_id"]
    for filename, url in (("corrected.docx", report["download_url"]), ("preview.pdf", report["preview_url"]),
                          ("original.docx", report["original_download_url"])):
        (job / filename).write_bytes(b"corrupted by synthetic test")
        response = client.get(url)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "artifact_changed"


@pytest.mark.external
def test_real_libreoffice_pdf_export(client):
    """Explicit opt-in; no network/LLM. Run in the Linux API container."""
    report = submit(client, render_pdf="true").json()
    assert report["correction"]["status"] == "applied"
    assert report["preview"]["status"] == "available", report["preview"]
    response = client.get(report["preview_url"])
    assert response.status_code == 200
    with pymupdf.open(stream=response.content, filetype="pdf") as pdf:
        assert pdf.page_count > 0
