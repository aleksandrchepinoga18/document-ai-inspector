"""Typed contracts for deterministic document inspection."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


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
    auto_fix_allowed: bool = False
    status: Literal["open", "undetermined", "fixed"] = "open"

    @model_validator(mode="after")
    def fixed_requires_permission(self):
        if self.status == "fixed" and not self.auto_fix_allowed:
            raise ValueError("A fixed finding must permit automatic correction")
        return self


class Edit(Contract):
    finding_id: UUID
    element_id: str
    property: Literal["font_size_pt"] = "font_size_pt"
    before: float
    after: float
    status: Literal["applied", "rejected"] = "applied"


class Correction(Contract):
    status: Literal["not_requested", "unchanged", "applied", "rejected"] = "not_requested"
    validation: Literal["not_run", "passed", "failed"] = "not_run"
    errors: list[str] = Field(default_factory=list)
    edits: list[Edit] = Field(default_factory=list)
    output_sha256: str | None = None


class Preview(Contract):
    status: Literal["not_requested", "available", "failed"] = "not_requested"
    source: Literal["original", "corrected"] = "original"
    pages: int | None = None
    sha256: str | None = None
    error_code: str | None = None
    message: str | None = None


class DocumentReport(Contract):
    job_id: UUID
    status: Literal["completed"] = "completed"
    filename: str
    original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile: str
    profile_version: str
    parser: Literal["python-docx"] = "python-docx"
    use_llm: Literal[False] = False
    auto_fix: bool = False
    render_pdf: bool = False
    elements: list[Element]
    findings: list[Finding]
    unsupported_features: list[UnsupportedFeature]
    metrics: dict[str, float | int]
    report_url: str
    download_url: str | None = None
    original_download_url: str | None = None
    preview_url: str | None = None
    correction: Correction = Field(default_factory=Correction)
    preview: Preview = Field(default_factory=Preview)
