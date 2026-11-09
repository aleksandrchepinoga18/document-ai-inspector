"""Only eligible font sizes change, including inherited formatting."""

import io
import zipfile

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Pt
from PIL import Image

from backend.core.config import Settings
from backend.documents.editor import apply_fixes
from backend.documents.parser import parse_document
from backend.documents.profiles import load_profile
from backend.documents.rules import inspect_document
from backend.documents.validator import validate_invariants
from samples.generate import build_document


def save(document):
    result = io.BytesIO()
    document.save(result)
    return result.getvalue()


def edit(data, profile=None):
    profile = profile or load_profile("default")
    parsed = parse_document(data)
    findings = inspect_document(parsed, profile)
    candidate, edits = apply_fixes(data, parsed, findings)
    return candidate, edits, profile


def test_only_font_size_changes_and_remaining_errors_are_visible():
    data = build_document("format_errors")
    candidate, edits, profile = edit(data)
    assert len(edits) == 1
    assert edits[0].before == 10 and edits[0].after == 14
    assert validate_invariants(data, candidate, edits, profile, Settings(_env_file=None)).passed
    remaining = inspect_document(parse_document(candidate), profile)
    assert {f.rule_id for f in remaining} == {"font_name", "alignment", "first_line_indent_mm"}
    assert Document(io.BytesIO(candidate)).paragraphs[1].runs[0].text == Document(io.BytesIO(data)).paragraphs[1].runs[0].text
    assert Document(io.BytesIO(candidate)).paragraphs[1].runs[0].font.name == "Arial"


def test_inherited_size_is_overridden_without_changing_shared_style(valid_docx):
    profile = load_profile("default")
    profile.paragraph.font_size_pt = 16
    candidate, edits, _ = edit(valid_docx, profile)
    assert len(edits) >= 1
    assert validate_invariants(valid_docx, candidate, edits, profile, Settings(_env_file=None)).passed
    with zipfile.ZipFile(io.BytesIO(valid_docx)) as original, zipfile.ZipFile(io.BytesIO(candidate)) as changed:
        assert original.read("word/styles.xml") == changed.read("word/styles.xml")
    assert Document(io.BytesIO(candidate)).paragraphs[1].runs[0].font.size.pt == 16


@pytest.mark.parametrize("kind", ["heading", "table", "field", "formula", "tracked"])
def test_complex_or_non_body_elements_are_not_edited(valid_docx, kind):
    document = Document(io.BytesIO(valid_docx))
    if kind == "heading":
        run = document.paragraphs[0].runs[0]
    elif kind == "table":
        run = document.tables[0].cell(0, 0).paragraphs[0].runs[0]
    else:
        paragraph = document.paragraphs[1]
        run = paragraph.runs[0]
        tag = {"field": "w:fldChar", "formula": "m:oMath", "tracked": "w:ins"}[kind]
        # Even an object in a DIFFERENT run excludes the entire paragraph.
        paragraph.add_run()._r.append(OxmlElement(tag))
    run.font.size = Pt(10)
    data = save(document)
    candidate, edits, _ = edit(data)
    assert candidate == data and edits == []


def test_images_formulas_tables_and_all_other_parts_are_preserved():
    document = Document(io.BytesIO(build_document("format_errors")))
    document.add_paragraph("Сумма 123.45, срок 17 дней.")
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(image, format="PNG")
    document.add_picture(io.BytesIO(image.getvalue()))
    math = OxmlElement("m:oMath")
    math_run = OxmlElement("m:r")
    text = OxmlElement("m:t")
    text.text = "x=42"
    math_run.append(text)
    math.append(math_run)
    document.add_paragraph()._p.append(math)
    data = save(document)
    candidate, edits, profile = edit(data)
    assert len(edits) == 1
    assert validate_invariants(data, candidate, edits, profile, Settings(_env_file=None)).passed
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(io.BytesIO(candidate)) as result:
        assert source.namelist() == result.namelist()
        assert any(name.startswith("word/media/") for name in source.namelist())
        assert all(source.read(name) == result.read(name) for name in source.namelist() if name != "word/document.xml")


def test_already_correct_document_is_unchanged(valid_docx):
    candidate, edits, _ = edit(valid_docx)
    assert candidate == valid_docx and edits == []


def test_superscript_run_is_not_edited(valid_docx):
    document = Document(io.BytesIO(valid_docx))
    run = document.paragraphs[1].runs[0]
    run.font.size = Pt(10)
    run.font.superscript = True
    data = save(document)
    candidate, edits, _ = edit(data)
    assert candidate == data and edits == []
