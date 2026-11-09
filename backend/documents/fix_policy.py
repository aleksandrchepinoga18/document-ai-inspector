"""The only permitted automatic edit: w:sz in an ordinary body text run."""

from docx.oxml.ns import qn

from backend.core.schemas import Finding
from backend.documents.parser import ParsedDocument, element_id, property_roots
from docx.text.paragraph import Paragraph
from docx.text.run import Run


def permitted_fixes(parsed: ParsedDocument, findings: list[Finding]) -> list[Finding]:
    elements = {item.id: item for item in parsed.elements}
    result = []
    body = parsed.document.element.body
    for finding in findings:
        if finding.source != "rule" or finding.rule_id != "font_size_pt" or finding.status != "open":
            continue
        item = elements.get(finding.element_id)
        if item is None or item.kind != "run" or not item.text.strip():
            continue
        paragraph = elements.get(item.parent_id)
        if paragraph is None or paragraph.kind != "paragraph" or not paragraph.properties.get("top_level"):
            continue
        if paragraph.style != "Normal" or item.style not in {None, "Default Paragraph Font"}:
            continue
        run = parsed.node_map[item.id]
        p_node = parsed.node_map[paragraph.id]
        if run.getparent() is not p_node or p_node.getparent() is not body:
            continue
        python_paragraph = Paragraph(p_node, parsed.document)
        special_properties = {qn("w:" + tag) for tag in ("vertAlign", "position", "rtl", "cs", "fitText")}
        if any(root is not None and any(child.tag in special_properties for child in root)
               for root in property_roots(python_paragraph, parsed.document, "rPr", Run(run, python_paragraph))):
            continue
        # Any unsupported object anywhere in the paragraph excludes the whole paragraph.
        if any(
            feature.feature != "additional_story" and (
                feature.element_id in {element_id(p) for p in [p_node, *p_node.iterancestors()]}
                or parsed.node_map[feature.element_id] is run
                or p_node in parsed.node_map[feature.element_id].iterancestors()
            ) for feature in parsed.unsupported
        ):
            continue
        if any(child.tag not in {qn("w:r"), qn("w:pPr")} for child in p_node):
            continue
        if any(child.tag not in {qn("w:rPr"), qn("w:t"), qn("w:tab"), qn("w:br")} for child in run):
            continue
        rprs = run.findall(qn("w:rPr"))
        sizes = rprs[0].findall(qn("w:sz")) if len(rprs) == 1 else []
        if len(rprs) > 1 or len(sizes) > 1:
            continue
        if sizes and (set(sizes[0].attrib) != {qn("w:val")} or len(sizes[0])):
            continue
        # Do not guess a new size for complex-script or unknown-format text.
        if not isinstance(finding.actual, (int, float)) or isinstance(finding.actual, bool):
            continue
        if not isinstance(finding.expected, (int, float)) or isinstance(finding.expected, bool):
            continue
        if not 1 <= finding.expected <= 200 or not float(finding.expected * 2).is_integer():
            continue
        if any("\u0590" <= c <= "\u0fff" or "\u2e80" <= c <= "\u9fff" for c in item.text):
            continue
        result.append(finding)
    return result
