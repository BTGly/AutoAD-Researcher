"""Structured explanatory sections; facts and tables remain deterministic."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NarrativeSectionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: Literal["summary", "interpretation", "limitations", "next_steps"]
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    claim_kind: Literal["explanation", "limitation", "recommendation"]


class NarrativeSectionsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    sections: list[NarrativeSectionV1]
