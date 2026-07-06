"""
Unified Research Context contracts (shared by Steps 3.2 and 3.3).

These contracts implement the ContextFact, InformationGap, ContextConflict,
ContextReadiness, ResearchContext, CandidateDecision, and
UnifiedResearchContextResult schemas from the 3.2 design plan.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern


# ---------------------------------------------------------------------------
# Sub-contexts
# ---------------------------------------------------------------------------


class TaskContext(BaseModel):
    """User task description and goal."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_id: str = Field(pattern=IdentifierPattern)
    goal: str = Field(min_length=1)
    domain: str | None = None
    constraints: list[str] = Field(default_factory=list)
    user_answers: dict[str, str] = Field(default_factory=dict)


class SourceContext(BaseModel):
    """Summary of available input sources."""

    model_config = ConfigDict(extra="forbid")

    paper_source_id: str | None = None
    repository_source_id: str | None = None
    has_user_input: bool = False
    has_environment_snapshot: bool = False


class PaperContext(BaseModel):
    """References to validated paper artifacts."""

    model_config = ConfigDict(extra="forbid")

    paper_reader_result_path: str | None = None
    paper_summary_path: str | None = None
    paper_candidates_path: str | None = None
    evidence_index_path: str | None = None


class RepositoryContext(BaseModel):
    """References to validated repository artifacts."""

    model_config = ConfigDict(extra="forbid")

    repository_result_path: str | None = None
    repository_identity_path: str | None = None
    evidence_index_path: str | None = None


class BaselineContext(BaseModel):
    """Resolved baseline information."""

    model_config = ConfigDict(extra="forbid")

    baseline_name: str | None = None
    baseline_source: Literal["paper_mentioned", "user_selected", "repository", "unknown"] = "unknown"
    resolved: bool = False


class DatasetContext(BaseModel):
    """Resolved dataset information."""

    model_config = ConfigDict(extra="forbid")

    dataset_name: str | None = None
    dataset_source: Literal["paper_mentioned", "user_selected", "repository", "unknown"] = "unknown"
    resolved: bool = False


class MetricContext(BaseModel):
    """Metric specification."""

    model_config = ConfigDict(extra="forbid")

    primary_metrics: list[str] = Field(default_factory=list)
    secondary_metrics: list[str] = Field(default_factory=list)


class ConstraintContext(BaseModel):
    """Environment, resource, and policy constraints."""

    model_config = ConfigDict(extra="forbid")

    gpu_required: bool = False
    memory_limit_mb: int | None = None
    max_runtime_seconds: int | None = None
    policy_restrictions: list[str] = Field(default_factory=list)


class UserPreferenceContext(BaseModel):
    """User-stated preferences."""

    model_config = ConfigDict(extra="forbid")

    preferences: dict[str, str] = Field(default_factory=dict)
    confirmed_decisions: list[str] = Field(default_factory=list)


class IdeaSourceContext(BaseModel):
    """A paper-derived or user-supplied idea source reference."""

    model_config = ConfigDict(extra="forbid")

    idea_source_id: str = Field(pattern=IdentifierPattern)
    title: str
    source: Literal["paper_derived", "user_supplied", "repository_inferred"]
    evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Source evidence and boundary
# ---------------------------------------------------------------------------


class SourceEvidenceRef(BaseModel):
    """Evidence item tying a context fact back to a concrete source attempt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: str = Field(pattern=IdentifierPattern)
    parse_attempt_id: str = Field(pattern=IdentifierPattern)
    artifact: str = Field(min_length=1)
    evidence_type: Literal[
        "parsed_full_text",
        "parse_quality",
        "repo_map",
        "user_text",
        "intake_error",
    ]


class EvidenceBoundary(BaseModel):
    """Known limits of the evidence available to the context draft."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    unparsed_sources: list[str] = Field(default_factory=list)
    partial_parse_attempts: list[str] = Field(default_factory=list)
    failed_parse_attempts: list[str] = Field(default_factory=list)
    claims_not_supported: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ContextFact
# ---------------------------------------------------------------------------


class ContextFact(BaseModel):
    """A single fact in the unified research context.

    Facts are tagged with their origin type and must not be silently
    overwritten by facts from a different source.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fact_id: str = Field(pattern=IdentifierPattern)
    fact_type: Literal[
        "paper_fact",
        "repository_fact",
        "user_fact",
        "environment_fact",
        "system_policy_fact",
        "derived_hypothesis",
    ]
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    value: Any
    status: Literal["confirmed", "inferred", "conflicting", "unknown"]
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[SourceEvidenceRef] = Field(default_factory=list)
    producer_stage: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# InformationGap
# ---------------------------------------------------------------------------


class InformationGap(BaseModel):
    """A gap or missing information in the research context.

    Gaps are classified by type, which determines the resolution target:
      - user_decision_required  → question_needed=true  → 3.3_context_repair
      - paper_evidence_required → question_needed=false → paper_intelligence
      - repository_evidence_required → question_needed=false → repository_intelligence
      - environment_evidence_required → question_needed=false → environment_profiler
      - system_policy_conflict  → question_needed=false → stop
      - unresolvable            → question_needed=false → stop
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    gap_id: str = Field(pattern=IdentifierPattern)
    gap_type: Literal[
        "user_decision_required",
        "paper_evidence_required",
        "repository_evidence_required",
        "environment_evidence_required",
        "system_policy_conflict",
        "unresolvable",
    ]
    category: Literal[
        "task_goal",
        "idea_scope",
        "baseline_selection",
        "dataset_selection",
        "metric_selection",
        "modification_boundary",
        "external_asset_permission",
        "resource_budget",
        "evaluation_protocol",
        "implementation_preference",
        "source_conflict",
        "policy_conflict",
        "reader_reanalysis_needed",
    ]
    severity: Literal["blocking", "high", "medium", "low"]
    question_needed: bool
    reason: str = Field(min_length=1)
    downstream_impact: str = Field(min_length=1)
    related_fact_ids: list[str] = Field(default_factory=list)
    related_evidence_ids: list[str] = Field(default_factory=list)
    resolution_target: Literal[
        "3.3_context_repair",
        "paper_intelligence",
        "repository_intelligence",
        "environment_profiler",
        "stop",
    ]
    resolved_by_patch_id: str | None = None


# ---------------------------------------------------------------------------
# ContextConflict
# ---------------------------------------------------------------------------


class ContextConflict(BaseModel):
    """A conflict between two or more source facts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conflict_id: str = Field(pattern=IdentifierPattern)
    conflict_type: Literal[
        "paper_vs_repository",
        "user_vs_paper",
        "user_vs_repository",
        "source_vs_policy",
        "paper_internal",
        "repository_internal",
    ]
    fact_ids: list[str] = Field(min_length=2)
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal[
        "unresolved",
        "user_interpretation_recorded",
        "resolved_by_evidence",
        "blocked",
    ] = "unresolved"
    selected_interpretation: str | None = None


# ---------------------------------------------------------------------------
# ContextReadiness
# ---------------------------------------------------------------------------


class ContextReadiness(BaseModel):
    """Readiness assessment for proceeding to Step 3.4."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "ready_for_idea_transfer_design",
        "needs_clarification",
        "needs_reader_reanalysis",
        "blocked_by_policy",
        "blocked_by_user",
    ]
    blocking_gap_ids: list[str] = Field(default_factory=list)
    unresolved_conflict_ids: list[str] = Field(default_factory=list)
    accepted_risk_ids: list[str] = Field(default_factory=list)
    reanalysis_targets: list[Literal[
        "paper_intelligence",
        "repository_intelligence",
        "environment_profiler",
    ]] = Field(default_factory=list)
    next_stage: Literal[
        "3.4_idea_transfer_design",
        "3.3_context_repair",
        "3.2_reanalysis",
        "stop",
    ]


# ---------------------------------------------------------------------------
# CandidateDecision
# ---------------------------------------------------------------------------


class CandidateDecision(BaseModel):
    """User-elected decisions on paper-mentioned candidates.

    Does NOT modify PaperMentionedCandidate.selection_status.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_id: str = Field(pattern=IdentifierPattern)
    decision: Literal[
        "confirmed_by_user",
        "rejected_by_user",
        "deferred",
        "not_asked",
    ]
    user_evidence_id: str | None = None
    decision_id: str | None = Field(default=None, pattern=IdentifierPattern)


# ---------------------------------------------------------------------------
# ResearchContext
# ---------------------------------------------------------------------------


class ResearchContext(BaseModel):
    """Draft or stable unified research context.

    Draft context may contain blocking gaps. Stable context is only
    generated after readiness passes or ContextPatch is applied.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: str = Field(pattern=IdentifierPattern)
    context_id: str = Field(pattern=IdentifierPattern)
    context_version: int = Field(ge=0)

    task: TaskContext
    sources: SourceContext
    paper: PaperContext | None = None
    repository: RepositoryContext | None = None
    baseline: BaselineContext | None = None
    dataset: DatasetContext | None = None
    metrics: MetricContext = Field(default_factory=MetricContext)
    constraints: ConstraintContext = Field(default_factory=ConstraintContext)
    user_preferences: UserPreferenceContext = Field(default_factory=UserPreferenceContext)
    idea_sources: list[IdeaSourceContext] = Field(default_factory=list)
    source_evidence: list[SourceEvidenceRef] = Field(default_factory=list)

    facts: list[ContextFact] = Field(default_factory=list)
    gaps: list[InformationGap] = Field(default_factory=list)
    conflicts: list[ContextConflict] = Field(default_factory=list)
    readiness: ContextReadiness

    evidence_index_refs: list[str] = Field(default_factory=list)
    evidence_boundary: EvidenceBoundary = Field(default_factory=EvidenceBoundary)
    previous_context_id: str | None = None
    context_sha256: str = Field(pattern=Sha256Pattern)


# ---------------------------------------------------------------------------
# UnifiedResearchContextResult
# ---------------------------------------------------------------------------


class UnifiedResearchContextResult(BaseModel):
    """Final result of the Unified Research Context Agent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    run_id: str = Field(pattern=IdentifierPattern)
    paper_capability_status: Literal[
        "success",
        "partial_success",
        "parse_failed",
        "failed",
        "not_requested",
    ]
    repository_capability_status: Literal[
        "success",
        "partial_success",
        "failed",
        "not_requested",
    ]
    context_readiness_status: Literal[
        "ready_for_idea_transfer_design",
        "needs_clarification",
        "needs_reader_reanalysis",
        "blocked_by_policy",
        "blocked_by_user",
    ]
    research_context_draft_path: str
    context_readiness_report_path: str
    stable_research_context_path: str | None = None
    idea_transfer_handoff_path: str | None = None
    next_stage: Literal[
        "3.4_idea_transfer_design",
        "3.3_context_repair",
        "3.2_reanalysis",
        "stop",
    ]
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# IdeaTransferHandoff
# ---------------------------------------------------------------------------


class IdeaTransferHandoff(BaseModel):
    """Structured handoff artifact to Step 3.4 Idea & Transfer Design.

    Contains the stable research context identity, user task goal,
    facts, gaps, conflicts, and readiness. Consumed by Step 3.4.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    run_id: str = Field(pattern=IdentifierPattern)
    context_id: str = Field(pattern=IdentifierPattern)
    context_version: int = Field(ge=0)
    context_sha256: str = Field(pattern=Sha256Pattern)

    task_goal: str = Field(min_length=1)
    facts: list[ContextFact] = Field(default_factory=list)
    gaps: list[InformationGap] = Field(default_factory=list)
    conflicts: list[ContextConflict] = Field(default_factory=list)
    readiness: ContextReadiness

    paper_source_id: str | None = None
    repository_source_id: str | None = None
    evidence_index_refs: list[str] = Field(default_factory=list)
