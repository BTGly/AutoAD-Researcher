"""Structured explanatory prose with explicit claim and fact bindings."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SectionId = Literal["summary", "interpretation", "limitations", "next_steps"]
ParagraphKind = Literal["background", "interpretation", "limitation", "recommendation"]
ClaimKind = Literal["explanation", "limitation", "recommendation"]


class StructuredClaimV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    claim_kind: ClaimKind
    statement_template: str = Field(min_length=1)
    fact_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class NarrativeParagraphV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraph_id: str = Field(min_length=1)
    paragraph_kind: ParagraphKind
    prose_template: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)


class NarrativeSectionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: SectionId
    paragraphs: list[NarrativeParagraphV1] = Field(min_length=1)


class NarrativeSectionsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    sections: list[NarrativeSectionV1]
    claims: list[StructuredClaimV1]
