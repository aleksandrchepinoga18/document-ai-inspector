"""Typed contracts for deterministic document inspection."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextFormat(Contract):
    font_name: str = Field(min_length=1)
    font_size_pt: float = Field(gt=0, le=200)
    first_line_indent_mm: float = Field(ge=-100, le=100)
    alignment: Literal["left", "center", "right", "justify"]


class RuleProfile(Contract):
    name: str = Field(pattern=r"^[a-z0-9_-]+$")
    version: str = Field(min_length=1)
    paragraph: TextFormat
    heading: TextFormat
    table: TextFormat
    heading_styles: dict[int, str]
    required_sections: list[str]
    table_caption_pattern: str


class Element(Contract):
    id: str
    kind: Literal["document", "paragraph", "heading", "run", "table"]
    text: str = ""
    style: str | None = None
    heading_level: int | None = None
    parent_id: str | None = None
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class UnsupportedFeature(Contract):
    feature: str
    element_id: str
    evidence: str
    auto_fix_allowed: Literal[False] = False


class Finding(Contract):
    id: UUID
    rule_id: str
    category: Literal["font", "paragraph", "structure"]
    severity: Literal["info", "warning", "error"]
    element_id: str
    locations: list[str] = Field(min_length=1)
    evidence: str
    actual: JsonValue
    expected: JsonValue
    source: Literal["rule", "llm"] = "rule"
    suggestion: str
    auto_fix_allowed: Literal[False] = False
    status: Literal["open", "undetermined"] = "open"


class DocumentReport(Contract):
    job_id: UUID
    status: Literal["completed"] = "completed"
    filename: str
    original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile: str
    profile_version: str
    parser: Literal["python-docx"] = "python-docx"
    use_llm: Literal[False] = False
    auto_fix: Literal[False] = False
    elements: list[Element]
    findings: list[Finding]
    unsupported_features: list[UnsupportedFeature]
    metrics: dict[str, float | int]
    report_url: str
