"""Control-signal contracts for Repository Intelligence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisControlSignal(BaseModel):
    """Agent request for the next analysis transition."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["continue_reading", "synthesis_ready", "blocked"]
    coverage: dict[
        str,
        Literal["confirmed", "checked_unknown", "conflicting", "not_checked"],
    ]
    new_evidence_count: int = Field(ge=0)
    unresolved_blockers: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
