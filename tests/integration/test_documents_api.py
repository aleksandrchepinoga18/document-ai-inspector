"""Actual multipart API -> worker -> persisted original/report, entirely offline."""

import hashlib
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app
from backend.documents.security import DOCX_MIME
from samples.generate import build_document


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "jobs")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as session:
        yield session


def upload(client, data, **fields):
    return client.post("/documents/check", files={"file": ("synthetic.docx", data, DOCX_MIME)}, data=fields)


def test_check_report_persistence_and_unchanged_original(client, settings):
    data = build_document("format_errors")
    response = upload(client, data, profile="default", use_llm="false", auto_fix="false")
    assert response.status_code == 201, response.text
    report = response.json()
    UUID(report["job_id"])
    assert len(report["findings"]) == 4
    assert report["profile_version"] == "1.0.0"
    assert report["original_sha256"] == hashlib.sha256(data).hexdigest()
    assert (settings.data_dir / report["job_id"] / "original.docx").read_bytes() == data
    assert client.get(report["report_url"]).json() == report
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(report["report_url"]).json() == report
    assert all(f["source"] == "rule" and f["locations"] for f in report["findings"])
    assert not report["use_llm"] and not report["auto_fix"]


def test_valid_document_has_no_false_findings(client, valid_docx):
    response = upload(client, valid_docx)
    assert response.status_code == 201, response.text
    assert response.json()["findings"] == []
    assert response.json()["unsupported_features"] == []


@pytest.mark.parametrize("fields", [
    {"use_llm": "true"}, {"provider": "ollama"},
])
def test_later_stage_modes_are_explicitly_rejected(client, valid_docx, fields):
    response = upload(client, valid_docx, **fields)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_mode"


def test_unknown_and_traversal_profiles_rejected(client, valid_docx):
    for name in ("missing-profile", "../default"):
        response = upload(client, valid_docx, profile=name)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "unknown_profile"


def test_broken_docx_has_clear_error(client):
    response = upload(client, b"this is not a document")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_docx"


def test_wrong_extension_rejected(client, valid_docx):
    response = client.post("/documents/check", files={"file": ("file.txt", valid_docx, DOCX_MIME)})
    assert response.status_code == 415


def test_unknown_or_invalid_job_id(client):
    assert client.get(f"/documents/{uuid4()}/report").status_code == 404
    assert client.get("/documents/not-a-uuid/report").status_code == 422


def test_size_limits_with_and_without_content_length(tmp_path, valid_docx):
    settings = Settings(_env_file=None, data_dir=tmp_path, max_upload_bytes=1024)
    with TestClient(create_app(settings)) as client:
        assert upload(client, valid_docx).status_code == 413
        # Force streaming multipart without a Content-Length header.
        header = (b'--test-boundary\r\nContent-Disposition: form-data; name="file"; '
                  b'filename="large.docx"\r\nContent-Type: application/octet-stream\r\n\r\n')
        chunks = iter((header, b"x" * 70000, b"\r\n--test-boundary--\r\n"))
        response = client.post(
            "/documents/check", content=chunks,
            headers={"Content-Type": "multipart/form-data; boundary=test-boundary"},
        )
        assert response.status_code == 413, response.text


def test_artifacts_for_unknown_jobs_do_not_exist(client):
    assert client.get(f"/documents/{uuid4()}/download").status_code == 404
    assert client.get(f"/documents/{uuid4()}/preview").status_code == 404
