"""Versioned synthetic documents and human-authored labels, independent of LLM output."""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Literal

from docx import Document
from pydantic import Field, model_validator

from backend.core.schemas import Contract
from backend.documents.parser import parse_document
from backend.llm.analyzer import extract_fragments
from backend.llm.schemas import Citation


DATASET_PATH = Path(__file__).parent / "datasets" / "synthetic-v1.json"
GENERATOR_VERSION = "synthetic-docx-v1"


class GoldEvent(Contract):
    error_type: Literal["contradiction"] = "contradiction"
    references: list[Citation] = Field(min_length=2)


class Case(Contract):
    document_id: str
    kind: Literal["positive", "negative", "ambiguous"]
    paragraphs: list[str] = Field(min_length=2)
    # Paragraph indices and exact evidence are authored in the source dataset.
    evidence: list[list[str]]
    rationale: str

    @model_validator(mode="after")
    def check_labels(self):
        if (self.kind == "positive") != bool(self.evidence):
            raise ValueError("Only positive cases have definite gold contradictions")
        for event in self.evidence:
            if len(event) != len(self.paragraphs) or any(q not in p or len(q) < 8 for q, p in zip(event, self.paragraphs)):
                raise ValueError("Gold evidence must quote each source paragraph")
        return self


class Dataset(Contract):
    version: str
    cases: list[Case]

    @model_validator(mode="after")
    def unique_ids(self):
        if len({c.document_id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate document_id")
        return self


def load_dataset() -> Dataset:
    return Dataset.model_validate_json(DATASET_PATH.read_text(encoding="utf-8"))


def dataset_hash(dataset: Dataset) -> str:
    return hashlib.sha256(json.dumps(dataset.model_dump(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def build_case(case: Case):
    document = Document()
    for index, text in enumerate(case.paragraphs, 1):
        document.add_heading(f"Раздел {index}", level=1)
        document.add_paragraph(text)
    raw = io.BytesIO()
    document.save(raw)
    # ZIP timestamps vary between invocations; normalize them for stable DOCX hashes.
    output = io.BytesIO()
    with zipfile.ZipFile(raw) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for name in sorted(source.namelist()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, source.read(name))
    data = output.getvalue()
    parsed = parse_document(data)
    paragraphs = [f for f in extract_fragments(parsed) if f.text in case.paragraphs]
    if [f.text for f in paragraphs] != case.paragraphs:
        raise ValueError("Generated paragraphs do not align with source labels")
    gold = [GoldEvent(references=[Citation(element_id=f.element_id, quote=q) for f, q in zip(paragraphs, event)])
            for event in case.evidence]
    return data, parsed, gold
