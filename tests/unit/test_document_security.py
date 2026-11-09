"""Reject malformed packages before python-docx can open them."""

import io
import zipfile

import pytest

from backend.core.config import Settings
from backend.documents.errors import DocumentError
from backend.documents.security import DOCX_MIME, validate_docx, validate_upload_metadata
from conftest import rewrite_zip


def assert_rejected(data, code, **limits):
    with pytest.raises(DocumentError) as exc:
        validate_docx(data, Settings(_env_file=None, **limits))
    assert exc.value.code == code


def test_valid_docx_is_accepted_without_mutation(valid_docx):
    original = bytes(valid_docx)
    validate_upload_metadata("пример.DOCX", DOCX_MIME)
    validate_docx(valid_docx, Settings(_env_file=None))
    assert valid_docx == original


@pytest.mark.parametrize("filename,mime", [
    ("report.docm", DOCX_MIME), ("../report.docx", DOCX_MIME),
    ("C:\\report.docx", DOCX_MIME), ("report.docx", "text/plain"),
    (None, DOCX_MIME),
])
def test_extension_filename_and_mime_are_checked(filename, mime):
    with pytest.raises(DocumentError) as exc:
        validate_upload_metadata(filename, mime)
    assert exc.value.status_code == 415


@pytest.mark.parametrize("payload,code", [(b"", "empty_file"), (b"not a zip", "invalid_docx")])
def test_invalid_upload(payload, code):
    assert_rejected(payload, code)


def test_upload_size_limit(valid_docx):
    assert_rejected(valid_docx, "file_too_large", max_upload_bytes=1024)


@pytest.mark.parametrize("path", ["../escaped.xml", "/absolute.xml", "C:/bad.xml", "dir\\bad.xml"])
def test_zip_paths_are_never_extracted(valid_docx, path):
    assert_rejected(rewrite_zip(valid_docx, {path: b"x"}), "unsafe_zip_path")


def test_duplicate_zip_entries_are_rejected(valid_docx):
    output = io.BytesIO(valid_docx)
    with zipfile.ZipFile(output, "a") as archive:
        with pytest.warns(UserWarning):
            archive.writestr("word/document.xml", b"duplicate")
    assert_rejected(output.getvalue(), "duplicate_zip_entry")


def test_missing_ooxml_parts(valid_docx):
    assert_rejected(rewrite_zip(valid_docx, {}, omit=("word/document.xml",)), "missing_ooxml_parts")


def test_zip_resource_limits(valid_docx):
    assert_rejected(valid_docx, "zip_entry_limit", max_zip_entries=1)
    assert_rejected(valid_docx, "zip_size_limit", max_uncompressed_bytes=1024)
    assert_rejected(valid_docx, "zip_ratio_limit", max_zip_ratio=1)


def test_xml_entities_and_dtd_are_rejected(valid_docx):
    malicious = b'''<!DOCTYPE document [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body><w:p><w:r><w:t>&secret;</w:t></w:r></w:p></w:body></w:document>'''
    assert_rejected(rewrite_zip(valid_docx, {"word/document.xml": malicious}), "unsafe_xml")


def test_xml_content_type_cannot_hide_dtd_behind_other_extension(valid_docx):
    from lxml import etree
    with zipfile.ZipFile(io.BytesIO(valid_docx)) as archive:
        types = etree.fromstring(archive.read("[Content_Types].xml"))
    etree.SubElement(
        types, "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
        PartName="/word/hidden.dat", ContentType="application/xml",
    )
    assert_rejected(rewrite_zip(valid_docx, {
        "[Content_Types].xml": etree.tostring(types),
        "word/hidden.dat": b'<!DOCTYPE x [<!ENTITY e "x">]><x>&e;</x>',
    }), "unsafe_xml")


def test_corrupt_xml(valid_docx):
    assert_rejected(rewrite_zip(valid_docx, {"word/document.xml": b"<broken>"}), "invalid_xml")


@pytest.mark.parametrize("mode,target,code", [
    ("External", "https://example.invalid/document", "external_relationship"),
    ("Internal", "../../outside.xml", "broken_relationship"),
    ("Internal", "missing.xml", "broken_relationship"),
])
def test_unsafe_relationships(valid_docx, mode, target, code):
    relationships = f'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="unsafe" Type="https://example.invalid/type" TargetMode="{mode}" Target="{target}"/>
    </Relationships>'''.encode()
    assert_rejected(rewrite_zip(valid_docx, {"word/_rels/unsafe.xml.rels": relationships}), code)


def test_macros_rejected_even_with_docx_extension(valid_docx):
    assert_rejected(rewrite_zip(valid_docx, {"word/vbaProject.bin": b"macro"}), "macros_not_allowed")
