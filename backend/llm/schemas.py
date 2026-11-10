"""Minimal model-facing schema: no editing commands or client-controlled permissions."""

from typing import Literal

from pydantic import Field

from backend.core.schemas import Contract


class Fragment(Contract):
    element_id: str
    section_id: str
    text: str


class Citation(Contract):
    element_id: str = Field(min_length=1, max_length=2000)
    quote: str = Field(min_length=8, max_length=1000)


class SemanticIssue(Contract):
    error_type: Literal["contradiction"]
    severity: Literal["warning", "error"] = "warning"
    references: list[Citation] = Field(min_length=2, max_length=4)
    explanation: str = Field(min_length=10, max_length=1500)
    suggestion: str = Field(min_length=3, max_length=1000)


class SemanticResponse(Contract):
    issues: list[SemanticIssue] = Field(max_length=20)
