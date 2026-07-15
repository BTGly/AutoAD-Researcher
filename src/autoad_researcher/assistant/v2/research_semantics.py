"""Shared semantic models for the generic research-intent protocol."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ResearchMode = Literal[
    "reproduction",
    "method_adaptation",
    "performance_optimization",
    "feasibility_assessment",
    "diagnosis",
    "open_research",
]
RequirementCategory = Literal[
    "objective",
    "research_object",
    "evaluation",
    "execution_boundary",
    "material",
    "safety",
]


class ResearchModeAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_mode: ResearchMode | None = None
    secondary_modes: list[ResearchMode] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""

    @model_validator(mode="after")
    def _dedupe_modes(self) -> "ResearchModeAssessment":
        self.secondary_modes = list(dict.fromkeys(
            mode for mode in self.secondary_modes if mode != self.primary_mode
        ))
        return self


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: RequirementCategory
    question: str = Field(min_length=1)
    required_now: bool
    rationale: str = ""


class EvidenceConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    status: Literal["blocking", "non_blocking"]
    evidence_refs: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class AdvisorySuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
