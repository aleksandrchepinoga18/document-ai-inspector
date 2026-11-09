"""Independent allowlist validation, including inversion of every approved patch."""

import copy
import io
import zipfile
from collections import Counter
from dataclasses import dataclass

from docx.oxml.ns import qn
from lxml import etree

from backend.core.config import Settings
from backend.core.schemas import Edit, RuleProfile
from backend.documents.editor import MAIN_PART, locate
from backend.documents.fix_policy import permitted_fixes
from backend.documents.parser import parse_document
from backend.documents.rules import inspect_document
from backend.documents.security import safe_xml, validate_docx


@dataclass
class InvariantResult:
    passed: bool
    errors: list[str]


def canonical(node):
    return etree.tostring(node, method="c14n", with_comments=True)


def package(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def validate_invariants(original: bytes, candidate: bytes, edits: list[Edit],
                        profile: RuleProfile, settings: Settings) -> InvariantResult:
    errors = []
    try:
        validate_docx(original, settings)
        validate_docx(candidate, settings)
        before, after = package(original), package(candidate)
        if before.keys() != after.keys():
            errors.append("package_parts_changed")
        if any(after.get(name) != payload for name, payload in before.items() if name != MAIN_PART):
            errors.append("protected_parts_changed")
        source = safe_xml(before[MAIN_PART])
        target = safe_xml(after[MAIN_PART])
        text_tags = {qn("w:t"), qn("w:delText"), qn("w:instrText")}
        text = lambda root: [(n.tag, n.text) for n in root.iter() if n.tag in text_tags]
        if text(source) != text(target):
            errors.append("text_changed")
        protected_counts = lambda root: Counter(
            n.tag for n in root.iter() if isinstance(n.tag, str) and n.tag not in {qn("w:rPr"), qn("w:sz")}
        )
        if protected_counts(source) != protected_counts(target):
            errors.append("structural_elements_changed")
        formula = lambda root: [canonical(n) for n in root.iter()
                                if n.tag == "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath"]
        if formula(source) != formula(target):
            errors.append("formulas_changed")

        parsed = parse_document(original)
        allowed = {f.id: f for f in permitted_fixes(parsed, inspect_document(parsed, profile))}
        seen = set()
        for edit in edits:
            finding = allowed.get(edit.finding_id)
            if (finding is None or edit.finding_id in seen or edit.element_id != finding.element_id
                    or edit.property != "font_size_pt" or edit.before != finding.actual
                    or edit.after != finding.expected or edit.status != "applied"):
                errors.append("unapproved_edit")
                continue
            seen.add(edit.finding_id)
            old_run, new_run = locate(source, edit.element_id), locate(target, edit.element_id)
            old_rpr, new_rpr = old_run.find(qn("w:rPr")), new_run.find(qn("w:rPr"))
            if len(new_run.findall(qn("w:rPr"))) != 1 or new_rpr is None:
                errors.append("invalid_size_patch")
                continue
            new_sizes = new_rpr.findall(qn("w:sz"))
            if (len(new_sizes) != 1 or new_sizes[0].attrib != {qn("w:val"): str(int(edit.after * 2))}
                    or len(new_sizes[0]) or new_sizes[0].text):
                errors.append("invalid_size_patch")
                continue
            old_size = old_rpr.find(qn("w:sz")) if old_rpr is not None else None
            expected_tail = old_size.tail if old_size is not None else None
            if new_sizes[0].tail != expected_tail:
                errors.append("unapproved_xml_change")
            if old_size is None:
                new_rpr.remove(new_sizes[0])
            else:
                new_rpr.replace(new_sizes[0], copy.deepcopy(old_size))
            if old_rpr is None:
                if len(new_rpr) or new_rpr.attrib or new_rpr.text or new_rpr.tail:
                    errors.append("unapproved_run_properties")
                else:
                    new_run.remove(new_rpr)
        # After undoing ONLY approved size patches, the complete XML must match.
        if canonical(source.getroottree()) != canonical(target.getroottree()):
            errors.append("unapproved_xml_change")
    except Exception:
        # A malformed package, broken mapping or unexpected validator failure is a veto.
        errors.append("invariant_validation_failed")
    return InvariantResult(not errors, list(dict.fromkeys(errors)))
