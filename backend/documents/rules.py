"""Deterministic rules; no mutation, LLM calls or auto-fix decisions."""

import re
import json
from uuid import NAMESPACE_URL, uuid5

from backend.core.schemas import Finding, RuleProfile
from backend.documents.parser import ParsedDocument, element_id


def normalized(text: str) -> str:
    return " ".join(text.strip().rstrip(":.").casefold().split())


def inspect_document(parsed: ParsedDocument, profile: RuleProfile) -> list[Finding]:
    findings = []
    elements = {item.id: item for item in parsed.elements}
    # Unsupported containers and their contents are not declared format errors.
    blocked = {
        item.element_id for item in parsed.unsupported
        if item.feature != "additional_story"
    }

    def is_blocked(identifier):
        node = parsed.node_map[identifier]
        return identifier in blocked or any(element_id(p) in blocked for p in node.iterancestors())

    def add(rule, item, actual, expected, category, unknown=False):
        seed = f"{parsed.sha256}:{profile.name}:{profile.version}:{rule}:{item.id}:{json.dumps(expected, ensure_ascii=False, sort_keys=True)}"
        findings.append(Finding(
            id=uuid5(NAMESPACE_URL, seed), rule_id=rule, category=category,
            severity="info" if unknown else "warning", element_id=item.id,
            locations=[item.id], evidence=item.text[:300], actual=actual, expected=expected,
            suggestion=("Проверьте свойство вручную: эффективное значение не определено."
                        if unknown else "Приведите свойство к значению профиля вручную."),
            status="undetermined" if unknown else "open",
        ))

    def compare(rule, item, expected, category):
        actual = item.properties.get(rule)
        if actual is None:
            add(rule, item, None, expected, category, unknown=True)
        elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            if abs(actual - expected) > 0.1:
                add(rule, item, actual, expected, category)
        elif str(actual).casefold() != str(expected).casefold():
            add(rule, item, actual, expected, category)

    for item in parsed.elements:
        if not item.text.strip() or is_blocked(item.id):
            continue
        if item.kind in {"paragraph", "heading"}:
            expected = profile.heading if item.kind == "heading" else (
                profile.table if item.properties.get("in_table") else profile.paragraph
            )
            compare("alignment", item, expected.alignment, "paragraph")
            compare("first_line_indent_mm", item, expected.first_line_indent_mm, "paragraph")
            if item.kind == "heading":
                style = profile.heading_styles.get(item.heading_level)
                if style is None or item.style != style:
                    add("heading_style", item, item.style, style or "Допустимый уровень заголовка", "structure")
        elif item.kind == "run":
            parent = elements[item.parent_id]
            expected = profile.heading if parent.kind == "heading" else (
                profile.table if parent.properties.get("in_table") else profile.paragraph
            )
            compare("font_name", item, expected.font_name, "font")
            compare("font_size_pt", item, expected.font_size_pt, "font")

    body = parsed.elements[0]
    top_paragraphs = [
        item for item in parsed.elements
        if item.kind in {"paragraph", "heading"} and item.properties.get("top_level")
    ]
    # If body content is unsupported, absence of a section cannot be established.
    if body.id not in blocked:
        for section in profile.required_sections:
            matches = [item for item in top_paragraphs if normalized(item.text) == normalized(section)]
            if not matches:
                add("required_section", body, None, section, "structure")
            else:
                for item in matches:
                    if is_blocked(item.id):
                        continue
                    if item.kind != "heading":
                        add("heading_style", item, item.style, profile.heading_styles.get(1), "structure")

    caption_pattern = re.compile(profile.table_caption_pattern)
    for item in parsed.elements:
        if item.kind != "table" or is_blocked(item.id):
            continue
        node = parsed.node_map[item.id]
        previous = node.getprevious()
        previous_id = element_id(previous) if previous is not None else None
        caption = elements.get(previous_id)
        if caption is not None and is_blocked(caption.id):
            continue
        if caption is None or caption.kind not in {"paragraph", "heading"} or not caption_pattern.search(caption.text):
            add("table_caption", item, caption.text[:300] if caption else None,
                profile.table_caption_pattern, "structure")
    return findings
