"""Generate three independent examples; never overwrite an existing file."""

import argparse
import io
import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Mm, Pt


def build_document(case: str) -> bytes:
    if case not in {"valid", "format_errors", "structure_errors"}:
        raise ValueError("Unknown synthetic case")
    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(14)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    normal.paragraph_format.first_line_indent = Mm(12.5)
    for name in ("Heading 1", "Heading 2"):
        heading = document.styles[name]
        heading.font.name = "Times New Roman"
        heading.font.size = Pt(14)
        # Template heading themes can override a font name at the same level.
        fonts = heading.element.rPr.rFonts
        for attr in list(fonts.attrib):
            if attr.endswith("Theme"):
                del fonts.attrib[attr]
        heading.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        heading.paragraph_format.first_line_indent = Mm(0)

    document.add_heading("Введение", level=1)
    paragraph = document.add_paragraph("Синтетический документ для проверки оформления. Данные вымышлены.")
    if case == "format_errors":
        paragraph.runs[0].font.name = "Arial"
        paragraph.runs[0].font.size = Pt(10)
        paragraph.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.first_line_indent = Mm(0)

    if case != "structure_errors":
        document.add_paragraph("Таблица 1 — План работ")
    table = document.add_table(rows=2, cols=2)
    for row, values in zip(table.rows, (("Этап", "Срок"), ("Проверка", "5 дней"))):
        for cell, value in zip(row.cells, values):
            cell.text = value
            for p in cell.paragraphs:
                p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
                p.paragraph_format.first_line_indent = Mm(0)
                for run in p.runs:
                    run.font.size = Pt(12)
    if case != "structure_errors":
        document.add_heading("Заключение", level=1)
        document.add_paragraph("Все сведения созданы специально для демонстрации.")
    result = io.BytesIO()
    document.save(result)
    return result.getvalue()


EXPECTED_RULES = {
    "valid": [],
    "format_errors": ["alignment", "first_line_indent_mm", "font_name", "font_size_pt"],
    "structure_errors": ["required_section", "table_caption"],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("results/samples"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        f"{case}.docx": build_document(case) for case in EXPECTED_RULES
    }
    payloads["expected.json"] = json.dumps({
        f"{case}.docx": {"finding_count": len(rules), "rule_ids": rules}
        for case, rules in EXPECTED_RULES.items()
    }, ensure_ascii=False, indent=2).encode("utf-8")
    collisions = [name for name in payloads if (args.output_dir / name).exists()]
    if collisions:
        parser.error("Файлы уже существуют; выберите новый --output-dir: " + ", ".join(collisions))
    for name, payload in payloads.items():
        with (args.output_dir / name).open("xb") as stream:
            stream.write(payload)
        print(args.output_dir / name)


if __name__ == "__main__":
    main()
