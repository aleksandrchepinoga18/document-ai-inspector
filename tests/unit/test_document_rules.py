"""Effective inheritance, stable locations and deterministic profile rules."""

import hashlib
import io

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from pydantic import ValidationError

from backend.core.schemas import Finding
from backend.documents.errors import DocumentError
from backend.documents.parser import parse_document
from backend.documents.profiles import load_profile
from backend.documents.rules import inspect_document
from samples.generate import EXPECTED_RULES, build_document


def save(document):
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize("case", list(EXPECTED_RULES))
def test_synthetic_cases_have_exact_expected_rules(case):
    data = build_document(case)
    parsed = parse_document(data)
    findings = inspect_document(parsed, load_profile("default"))
    assert sorted(f.rule_id for f in findings) == sorted(EXPECTED_RULES[case])
    assert all(f.status == "open" and not f.auto_fix_allowed for f in findings)
    assert parsed.sha256 == hashlib.sha256(data).hexdigest()
    assert all(location in parsed.node_map for f in findings for location in f.locations)


def test_stable_ids_mapping_and_table_text(valid_docx):
    first, second = parse_document(valid_docx), parse_document(valid_docx)
    assert [e.id for e in first.elements] == [e.id for e in second.elements]
    assert len(first.node_map) == len(first.elements)
    assert all(node.getroottree().xpath(identifier.split(":", 1)[1], namespaces=node.nsmap)[0] is node
               for identifier, node in first.node_map.items())
    assert any(e.kind == "table" and "5 дней" in e.text for e in first.elements)
    assert any(e.kind == "heading" and e.heading_level == 1 for e in first.elements)


def test_element_count_limit(valid_docx):
    with pytest.raises(DocumentError) as exc:
        parse_document(valid_docx, max_elements=2)
    assert exc.value.code == "document_element_limit"


def test_base_and_character_style_inheritance(valid_docx):
    document = Document(io.BytesIO(valid_docx))
    paragraph = document.paragraphs[1]
    derived = document.styles.add_style("Inherited Body", WD_STYLE_TYPE.PARAGRAPH)
    derived.base_style = document.styles["Normal"]
    paragraph.style = derived
    character = document.styles.add_style("Inherited Text", WD_STYLE_TYPE.CHARACTER)
    character.font.size = Pt(14)
    paragraph.runs[0].style = character
    assert paragraph.runs[0].font.size is None
    assert paragraph.paragraph_format.alignment is None
    assert inspect_document(parse_document(save(document)), load_profile("default")) == []


def test_unknown_property_is_info_not_a_format_error(valid_docx):
    document = Document(io.BytesIO(valid_docx))
    paragraph = document.paragraphs[1]
    paragraph.style.font.size = None
    defaults = document.styles.element.find(qn("w:docDefaults"))
    for size in defaults.iter(qn("w:sz")):
        size.getparent().remove(size)
    findings = inspect_document(parse_document(save(document)), load_profile("default"))
    unknown = [f for f in findings if f.rule_id == "font_size_pt"]
    assert unknown
    assert all(f.status == "undetermined" and f.severity == "info" and f.actual is None for f in unknown)


def test_absent_styles_do_not_create_fictitious_template_defaults(valid_docx):
    document = Document(io.BytesIO(valid_docx))
    style_rel = next(key for key, rel in document.part.rels.items() if rel.reltype.endswith("/styles"))
    document.part.drop_rel(style_rel)
    parsed = parse_document(save(document))
    body_run = next(e for e in parsed.elements if e.kind == "run" and "Синтетический" in e.text)
    assert body_run.properties["font_size_pt"] is None
    assert body_run.properties["font_name"] is None
    assert not any(rel.reltype.endswith("/styles") for rel in parsed.document.part.rels.values())


def test_profile_changes_result_without_changing_document(valid_docx):
    profile = load_profile("default")
    profile.paragraph.font_size_pt = 13
    parsed = parse_document(valid_docx)
    findings = inspect_document(parsed, profile)
    assert any(f.rule_id == "font_size_pt" and f.expected == 13 and f.actual == 14 for f in findings)


def test_required_section_must_have_heading_style(valid_docx):
    document = Document(io.BytesIO(valid_docx))
    document.paragraphs[0].style = "Normal"
    findings = inspect_document(parse_document(save(document)), load_profile("default"))
    assert any(f.rule_id == "heading_style" and f.evidence == "Введение" for f in findings)


def test_multiple_missing_sections_have_distinct_finding_ids(valid_docx):
    document = Document(io.BytesIO(valid_docx))
    for p in document.paragraphs:
        if p.text in {"Введение", "Заключение"}:
            p._p.getparent().remove(p._p)
    findings = inspect_document(parse_document(save(document)), load_profile("default"))
    missing = [f for f in findings if f.rule_id == "required_section"]
    assert len(missing) == 2 and len({f.id for f in missing}) == 2


@pytest.mark.parametrize("tag,feature", [("w:fldChar", "fields"), ("m:oMath", "formula"), ("w:ins", "track_changes")])
def test_unsupported_objects_are_marked(valid_docx, tag, feature):
    document = Document(io.BytesIO(valid_docx))
    document.paragraphs[1].runs[0]._r.append(OxmlElement(tag))
    parsed = parse_document(save(document))
    assert any(f.feature == feature and not f.auto_fix_allowed for f in parsed.unsupported)


def test_merged_tables_are_marked_and_not_misdiagnosed(valid_docx):
    document = Document(io.BytesIO(valid_docx))
    document.tables[0].cell(0, 0).merge(document.tables[0].cell(0, 1))
    parsed = parse_document(save(document))
    feature = next(f for f in parsed.unsupported if f.feature == "merged_table")
    assert feature.element_id in parsed.node_map
    assert not any(f.rule_id == "table_caption" for f in inspect_document(parsed, load_profile("default")))


def test_finding_schema_forbids_unimplemented_auto_fix():
    finding = inspect_document(parse_document(build_document("format_errors")), load_profile("default"))[0]
    with pytest.raises(ValidationError):
        Finding.model_validate({**finding.model_dump(), "auto_fix_allowed": True})
