"""Fault injection: plausible but unsafe edits must never be approved."""

import io
import zipfile

import pytest
from docx.oxml.ns import qn
from lxml import etree

from backend.core.config import Settings
from backend.documents.editor import apply_fixes
from backend.documents.parser import parse_document
from backend.documents.profiles import load_profile
from backend.documents.rules import inspect_document
from backend.documents.validator import validate_invariants
from conftest import rewrite_zip
from samples.generate import build_document


@pytest.fixture
def corrected():
    data = build_document("format_errors")
    profile = load_profile("default")
    parsed = parse_document(data)
    candidate, edits = apply_fixes(data, parsed, inspect_document(parsed, profile))
    return data, candidate, edits, profile


@pytest.mark.parametrize("tampering", ["text", "number", "table", "paragraph_property", "size", "new_run", "xml_attribute"])
def test_unapproved_main_xml_changes_are_vetoed(corrected, tampering):
    original, candidate, edits, profile = corrected
    with zipfile.ZipFile(io.BytesIO(candidate)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    if tampering == "text":
        next(root.iter(qn("w:t"))).text = "Подменённое содержание"
    elif tampering == "number":
        node = next(n for n in root.iter(qn("w:t")) if n.text == "5 дней")
        node.text = "999 дней"
    elif tampering == "table":
        node = next(root.iter(qn("w:tbl")))
        node.getparent().remove(node)
    elif tampering == "paragraph_property":
        next(root.iter(qn("w:pPr"))).set("unapproved", "true")
    elif tampering == "size":
        next(n for n in root.iter(qn("w:sz"))).set(qn("w:val"), "99")
    elif tampering == "new_run":
        etree.SubElement(next(root.iter(qn("w:p"))), qn("w:r"))
    else:
        root.set("unapproved", "true")
    broken = rewrite_zip(candidate, {"word/document.xml": etree.tostring(root)})
    result = validate_invariants(original, broken, edits, profile, Settings(_env_file=None))
    assert not result.passed and result.errors


@pytest.mark.parametrize("part", ["word/styles.xml", "word/_rels/document.xml.rels", "docProps/core.xml"])
def test_every_noneditable_part_is_byte_protected(corrected, part):
    original, candidate, edits, profile = corrected
    with zipfile.ZipFile(io.BytesIO(candidate)) as archive:
        payload = archive.read(part)
    broken = rewrite_zip(candidate, {part: payload + b"\n"})
    result = validate_invariants(original, broken, edits, profile, Settings(_env_file=None))
    assert not result.passed
    assert "protected_parts_changed" in result.errors


def test_added_parts_are_rejected(corrected):
    original, candidate, edits, profile = corrected
    broken = rewrite_zip(candidate, {"word/new.bin": b"new part"})
    result = validate_invariants(original, broken, edits, profile, Settings(_env_file=None))
    assert not result.passed and "package_parts_changed" in result.errors


def test_missing_or_forged_edit_log_is_rejected(corrected):
    original, candidate, edits, profile = corrected
    assert not validate_invariants(original, candidate, [], profile, Settings(_env_file=None)).passed
    forged = edits[0].model_copy(update={"after": 11})
    assert not validate_invariants(original, candidate, [forged], profile, Settings(_env_file=None)).passed
    assert not validate_invariants(original, candidate, edits * 2, profile, Settings(_env_file=None)).passed


def test_malformed_candidate_is_a_validation_failure(corrected):
    original, _, edits, profile = corrected
    result = validate_invariants(original, b"not a zip", edits, profile, Settings(_env_file=None))
    assert not result.passed


@pytest.mark.parametrize("object_type", ["formula", "image"])
def test_formula_content_and_embedded_media_are_protected(object_type):
    original = build_document("protected_objects")
    profile = load_profile("default")
    parsed = parse_document(original)
    candidate, edits = apply_fixes(original, parsed, inspect_document(parsed, profile))
    with zipfile.ZipFile(io.BytesIO(candidate)) as archive:
        if object_type == "formula":
            root = etree.fromstring(archive.read("word/document.xml"))
            next(root.iter("{http://schemas.openxmlformats.org/officeDocument/2006/math}t")).text = "x=999"
            replacements = {"word/document.xml": etree.tostring(root)}
        else:
            name = next(n for n in archive.namelist() if n.startswith("word/media/"))
            replacements = {name: b"changed synthetic image"}
    result = validate_invariants(original, rewrite_zip(candidate, replacements), edits, profile, Settings(_env_file=None))
    assert not result.passed
    expected = "formulas_changed" if object_type == "formula" else "protected_parts_changed"
    assert expected in result.errors
