"""Surgical ZIP patching: never save the whole package with python-docx."""

import copy
import io
import zipfile

from docx.oxml.ns import qn
from lxml import etree

from backend.core.schemas import Edit, Finding
from backend.documents.fix_policy import permitted_fixes
from backend.documents.parser import ParsedDocument
from backend.documents.security import safe_xml


MAIN_PART = "word/document.xml"


def locate(root, identifier):
    part, xpath = identifier.split(":", 1)
    if part != MAIN_PART:
        raise ValueError("Only the main document is editable")
    nodes = root.getroottree().xpath(xpath, namespaces={k: v for k, v in root.nsmap.items() if k})
    if len(nodes) != 1 or nodes[0].tag != qn("w:r"):
        raise ValueError("Run mapping is not unique")
    return nodes[0]


def apply_fixes(data: bytes, parsed: ParsedDocument, findings: list[Finding]) -> tuple[bytes, list[Edit]]:
    allowed = permitted_fixes(parsed, findings)
    if not allowed:
        return data, []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = safe_xml(archive.read(MAIN_PART))
        edits = []
        for finding in allowed:
            run = locate(root, finding.element_id)
            rpr = run.find(qn("w:rPr"))
            if rpr is None:
                rpr = etree.Element(qn("w:rPr"))
                run.insert(0, rpr)
            size = rpr.find(qn("w:sz"))
            if size is None:
                size = etree.Element(qn("w:sz"))
                # Respect the OOXML run-property order when adding w:sz.
                successors = {qn("w:" + tag) for tag in (
                    "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
                    "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
                )}
                index = next((i for i, node in enumerate(rpr) if node.tag in successors), len(rpr))
                rpr.insert(index, size)
            size.set(qn("w:val"), str(int(finding.expected * 2)))
            edits.append(Edit(
                finding_id=finding.id, element_id=finding.element_id,
                before=finding.actual, after=finding.expected,
            ))
        patched = etree.tostring(root.getroottree(), xml_declaration=True, encoding="UTF-8", standalone=True)
        result = io.BytesIO()
        with zipfile.ZipFile(result, "w") as output:
            output.comment = archive.comment
            for entry in archive.infolist():
                payload = patched if entry.filename == MAIN_PART else archive.read(entry.filename)
                output.writestr(copy.copy(entry), payload)
    return result.getvalue(), edits
