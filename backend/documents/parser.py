"""Read-only main-document parser with version-scoped XPath identities."""

import hashlib
import io
from dataclasses import dataclass

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from lxml import etree

from backend.core.schemas import Element, UnsupportedFeature
from backend.documents.errors import DocumentError


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


@dataclass
class ParsedDocument:
    sha256: str
    elements: list[Element]
    node_map: dict[str, etree._Element]
    unsupported: list[UnsupportedFeature]
    document: object


def element_id(node: etree._Element) -> str:
    return "word/document.xml:" + node.getroottree().getpath(node)


def style_chain(style):
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        yield style
        style = style.base_style


def existing_styles(document):
    # document.styles would synthesize a default styles part when it is absent.
    # Such defaults are not evidence of the input document's formatting.
    for rel in document.part.rels.values():
        if rel.reltype.endswith("/styles"):
            return rel.target_part.styles
    return None


def property_roots(paragraph, document, kind, run=None):
    """Nearest explicit property wins; cycles in style inheritance are bounded."""
    styles = existing_styles(document)
    if run is not None:
        yield run._element.find(qn("w:rPr"))
        for style in style_chain(run.style if styles is not None else None):
            yield style.element.find(qn("w:rPr"))
    if kind == "pPr":
        yield paragraph._p.find(qn("w:pPr"))
    for style in style_chain(paragraph.style if styles is not None else None):
        yield style.element.find(qn("w:" + kind))
    defaults = styles.element.find(qn("w:docDefaults")) if styles is not None else None
    if defaults is not None:
        wrapper = defaults.find(qn("w:" + kind + "Default"))
        if wrapper is not None:
            yield wrapper.find(qn("w:" + kind))


def first_value(roots, child, attr="val"):
    for root in roots:
        if root is None:
            continue
        node = root.find(qn("w:" + child))
        if node is not None and node.get(qn("w:" + attr)) is not None:
            return node.get(qn("w:" + attr))
    return None


def theme_fonts(document):
    fonts = {}
    for rel in document.part.rels.values():
        if rel.reltype.endswith("/theme"):
            # Security validation has already rejected all DTD/entities.
            from backend.documents.security import safe_xml
            root = safe_xml(rel.target_part.blob)
            for family in ("major", "minor"):
                latin = root.find(f".//{{{A}}}{family}Font/{{{A}}}latin")
                if latin is not None:
                    fonts[family + "Ascii"] = latin.get("typeface")
                    fonts[family + "HAnsi"] = latin.get("typeface")
    return fonts


def effective_font(roots, fonts, text):
    roots = list(roots)
    # Complex and East Asian scripts require language-specific theme rules.
    if any("\u0590" <= c <= "\u0fff" or "\u2e80" <= c <= "\u9fff" for c in text):
        return None
    resolved = []
    slots = ["ascii"] if text.isascii() else ["hAnsi"]
    if not text.isascii() and any(c.isascii() and c.isalpha() for c in text):
        slots.append("ascii")
    for slot in slots:
        value = None
        for root in roots:
            node = root.find(qn("w:rFonts")) if root is not None else None
            if node is None:
                continue
            theme = node.get(qn("w:" + slot + "Theme"))
            direct = node.get(qn("w:" + slot))
            if theme is not None or direct is not None:
                value = fonts.get(theme) if theme is not None else direct
                break
        resolved.append(value)
    return resolved[0] if resolved and all(v == resolved[0] for v in resolved) else None


def paragraph_properties(paragraph, document):
    roots = list(property_roots(paragraph, document, "pPr"))
    alignment = first_value(roots, "jc")
    # Character-based indentation takes precedence over twip-based indentation.
    indent = None
    for root in roots:
        node = root.find(qn("w:ind")) if root is not None else None
        if node is None:
            continue
        if any(node.get(qn("w:" + attr)) is not None for attr in ("firstLineChars", "hangingChars")):
            break
        if node.get(qn("w:hanging")) is not None:
            indent = -int(node.get(qn("w:hanging"))) * 25.4 / 1440
            break
        if node.get(qn("w:firstLine")) is not None:
            indent = int(node.get(qn("w:firstLine"))) * 25.4 / 1440
            break
    outline = first_value(roots, "outlineLvl")
    level = int(outline) + 1 if outline is not None and 0 <= int(outline) <= 8 else None
    return {
        "alignment": {"both": "justify", "start": "left", "end": "right"}.get(alignment, alignment),
        "first_line_indent_mm": round(indent, 3) if indent is not None else None,
    }, level


def parse_document(data: bytes, max_elements: int = 20000) -> ParsedDocument:
    """Call only after validate_docx. Never save/mutate the python-docx object."""
    try:
        document = Document(io.BytesIO(data))
        body = document.element.body
        styles = existing_styles(document)
        known_styles = {s.style_id for s in styles} if styles is not None else set()
        if sum(node.tag in {qn("w:p"), qn("w:tbl"), qn("w:r")} for node in body.iter()) + 1 > max_elements:
            raise DocumentError("document_element_limit", "Слишком много элементов в документе.")
        fonts = theme_fonts(document)
        elements = [Element(id=element_id(body), kind="document")]
        node_map = {elements[0].id: body}
        for node in body.iter():
            if node.tag not in {qn("w:p"), qn("w:tbl")}:
                continue
            identifier = element_id(node)
            node_map[identifier] = node
            parent_id = next(
                (element_id(p) for p in node.iterancestors() if element_id(p) in node_map),
                elements[0].id,
            )
            if node.tag == qn("w:tbl"):
                elements.append(Element(
                    id=identifier, kind="table", parent_id=parent_id,
                    text=" | ".join(
                        t.text or "" for t in node.iter(qn("w:t"))
                    ),
                    properties={"rows": len(node.findall(qn("w:tr")))},
                ))
                continue
            paragraph = Paragraph(node, document)
            props, level = paragraph_properties(paragraph, document)
            style = paragraph.style if styles is not None else None
            props["in_table"] = any(p.tag == qn("w:tbl") for p in node.iterancestors())
            props["top_level"] = node.getparent() is body
            elements.append(Element(
                id=identifier, kind="heading" if level else "paragraph",
                text=paragraph.text, style=style.name if style else None,
                heading_level=level, parent_id=parent_id, properties=props,
            ))
            for run_node in node.iter(qn("w:r")):
                # Runs in text boxes belong to their own nested paragraphs.
                nearest_p = next(p for p in run_node.iterancestors() if p.tag == qn("w:p"))
                if nearest_p is not node:
                    continue
                run = Run(run_node, paragraph)
                run_style = run.style if styles is not None else None
                run_id = element_id(run_node)
                node_map[run_id] = run_node
                roots = list(property_roots(paragraph, document, "rPr", run))
                size = first_value(roots, "sz")
                elements.append(Element(
                    id=run_id, kind="run", text=run.text, parent_id=identifier,
                    style=run_style.name if run_style else None,
                    properties={
                        "font_name": effective_font(roots, fonts, run.text),
                        "font_size_pt": int(size) / 2 if size is not None else None,
                    },
                ))

        unsupported = []
        seen = set()

        def warn(node, feature, evidence):
            target = next(
                (element_id(p) for p in [node, *node.iterancestors()] if element_id(p) in node_map),
                elements[0].id,
            )
            if (feature, target) not in seen:
                seen.add((feature, target))
                unsupported.append(UnsupportedFeature(feature=feature, element_id=target, evidence=evidence))

        tags = {
            "fldChar": "fields", "fldSimple": "fields", "instrText": "fields",
            "footnoteReference": "footnotes", "endnoteReference": "endnotes",
            "ins": "track_changes", "del": "track_changes",
            "moveFrom": "track_changes", "moveTo": "track_changes",
            "gridSpan": "merged_table", "vMerge": "merged_table",
            "drawing": "drawing", "pict": "drawing", "object": "embedded_object",
            "txbxContent": "text_box", "numPr": "numbering",
            "sdt": "content_control", "altChunk": "alternative_content",
        }
        for node in body.iter():
            if not isinstance(node.tag, str):
                continue
            local = etree.QName(node).localname
            feature = tags.get(local)
            if local in {"oMath", "oMathPara"}:
                feature = "formula"
            if local.endswith("PrChange"):
                feature = "track_changes"
            if feature:
                warn(node, feature, f"OOXML {local}: безопасная правка не поддерживается.")
            if node.tag == qn("w:tbl") and any(p.tag == qn("w:tbl") for p in node.iterancestors()):
                warn(node, "nested_table", "Вложенная таблица не поддерживается для безопасной правки.")

        for item in elements:
            if item.kind not in {"paragraph", "heading"}:
                continue
            node = node_map[item.id]
            paragraph = Paragraph(node, document)
            for reference in node.xpath("./w:pPr/w:pStyle | .//w:rPr/w:rStyle"):
                if reference.get(qn("w:val")) not in known_styles:
                    warn(node, "unresolved_style", "Ссылка на отсутствующий стиль: форматирование не определено.")
            if any(
                root is not None and root.find(qn("w:bidi")) is not None
                for root in property_roots(paragraph, document, "pPr")
            ):
                warn(node, "bidirectional_text", "Параметры RTL требуют отдельного разрешения форматирования.")
            if any(
                root is not None and root.find(qn("w:numPr")) is not None
                for root in property_roots(paragraph, document, "pPr")
            ):
                warn(node, "numbering", "Отступы списков требуют разрешения numbering.xml.")
            for table_node in (p for p in node.iterancestors() if p.tag == qn("w:tbl")):
                style_node = table_node.find("./" + qn("w:tblPr") + "/" + qn("w:tblStyle"))
                if style_node is None:
                    continue
                style_id = style_node.get(qn("w:val"))
                table_style = next((s for s in styles if s.style_id == style_id), None) if styles is not None else None
                if table_style is None or any(
                    s.element.xpath(".//w:rPr | .//w:pPr") for s in style_chain(table_style)
                ):
                    warn(table_node, "table_style_formatting", "Условное форматирование стиля таблицы не разрешается.")

        for part in document.part.package.parts:
            name = str(part.partname)
            if any(token in name for token in ("/header", "/footer", "/footnotes", "/endnotes", "/comments")):
                warn(body, "additional_story", f"Часть {name} не входит в формальную проверку основного текста.")
        return ParsedDocument(hashlib.sha256(data).hexdigest(), elements, node_map, unsupported, document)
    except DocumentError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, etree.LxmlError) as exc:
        raise DocumentError("unsupported_document", "Структура или свойства DOCX повреждены либо не поддерживаются.") from exc
