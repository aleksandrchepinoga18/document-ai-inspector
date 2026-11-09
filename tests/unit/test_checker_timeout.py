"""A hung worker cannot hold a request forever or alter the original."""

import subprocess

import pytest

from backend.core.config import Settings
from backend.documents.errors import DocumentError
from backend.documents.profiles import load_profile
from backend.pipeline.checker import check_document


def test_worker_timeout_preserves_original(tmp_path, valid_docx, monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 1
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr("backend.pipeline.checker.subprocess.run", timeout)
    settings = Settings(_env_file=None, data_dir=tmp_path, processing_timeout_seconds=1)
    with pytest.raises(DocumentError) as exc:
        check_document(valid_docx, "synthetic.docx", load_profile("default"), settings)
    assert exc.value.status_code == 504
    job = next(tmp_path.iterdir())
    assert (job / "original.docx").read_bytes() == valid_docx
    assert (job / "failure.json").is_file()
    assert not (job / "report.json").exists()
