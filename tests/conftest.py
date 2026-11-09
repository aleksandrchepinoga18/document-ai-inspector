"""In-memory synthetic documents: no fixtures containing user data."""

import io
import zipfile

import pytest

from samples.generate import build_document


@pytest.fixture
def valid_docx():
    return build_document("valid")


def rewrite_zip(data: bytes, replacements: dict[str, bytes], omit=()) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED,
    ) as target:
        for item in source.infolist():
            if item.filename not in omit and item.filename not in replacements:
                target.writestr(item.filename, source.read(item.filename))
        for name, payload in replacements.items():
            target.writestr(name, payload)
    return output.getvalue()
