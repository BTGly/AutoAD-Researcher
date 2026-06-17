"""Control-signal contracts for Paper Intelligence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.paper_intelligence.ids import IdentifierPattern


class PaperAnalysisControlSignal(BaseModel):
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


class PaperIntelligenceStatus:
    """State machine states for Paper Intelligence Capability."""

    REQUEST_RECEIVED = "request_received"
    SOURCE_ATTESTING = "source_attesting"
    PARSE_RUNNING = "parse_running"
    PARSE_FAILED = "parse_failed"
    PARSE_PARTIAL = "parse_partial"
    PARSE_READY = "parse_ready"
    ANALYSIS_RUNNING = "analysis_running"
    SYNTHESIS_RUNNING = "synthesis_running"
    VALIDATION_RUNNING = "validation_running"
    REPAIR_RUNNING = "repair_running"
    PAPER_CAPABILITY_READY = "paper_capability_ready"

    TERMINAL = {"success", "partial_success", "failed"}


class AnalysisProgress(BaseModel):
    """Tracks analysis progress across the paper."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=IdentifierPattern)
    cycle: int = Field(ge=0)
    sections_visited: list[str] = Field(default_factory=list)
    sections_remaining: list[str] = Field(default_factory=list)
    total_evidence_count: int = Field(ge=0)
    coverage: dict[
        str,
        Literal["confirmed", "checked_unknown", "conflicting", "not_checked"],
    ] = Field(default_factory=dict)
    status: Literal[
        "reading",
        "synthesizing",
        "validating",
        "repairing",
        "complete",
    ] = "reading"
