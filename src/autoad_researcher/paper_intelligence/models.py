"""Core Paper Intelligence contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoad_researcher.paper_intelligence.evidence_models import PaperEvidenceRef
from autoad_researcher.paper_intelligence.ids import IdentifierPattern, LegacyPaperSourceIdPattern, Sha256Pattern, validate_workspace_path


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


class PaperAgentBudget(BaseModel):
    """Paper Agent budget with separate analysis and repair reserves."""

    model_config = ConfigDict(extra="forbid")

    max_total_tool_calls: int = Field(ge=0)
    max_total_llm_calls: int = Field(ge=0)
    max_total_input_tokens: int = Field(ge=0)
    max_total_output_tokens: int = Field(ge=0)

    max_parse_attempts: int = Field(ge=0)
    max_analysis_llm_calls: int = Field(ge=0)
    max_analysis_reads: int = Field(ge=0)
    max_analysis_search_calls: int = Field(ge=0)
    max_web_fetch_calls: int = Field(ge=0)

    max_repair_tool_calls: int = Field(ge=0)
    max_repair_llm_calls: int = Field(ge=0)
    max_repairs: int = Field(ge=0)

    max_no_progress_cycles: int = Field(default=2, ge=0)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class PaperIntelligenceRequest(BaseModel):
    """Input contract for Paper Intelligence Capability."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    request_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    user_goal: str = Field(min_length=1)

    paper_pdf_path: str
    paper_url: str | None = None
    arxiv_id: str | None = None
    title_hint: str | None = None
    authors_hint: list[str] = Field(default_factory=list)

    parser_profile_id: str = Field(pattern=IdentifierPattern)
    web_context_allowed: bool = False
    alpha_xiv_allowed: bool = False

    user_confirmation_policy: Literal["always", "when_ambiguous", "never"]
    budget_profile: Literal["short", "standard", "long", "custom"]
    budget: PaperAgentBudget | None = None

    @field_validator("paper_pdf_path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return validate_workspace_path(value)

    @model_validator(mode="after")
    def _validate_custom_budget(self):
        if self.budget_profile == "custom" and self.budget is None:
            raise ValueError("custom budget_profile requires budget")
        return self


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class PaperSource(BaseModel):
    """Attested paper source identity."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    source_id: str = Field(pattern=LegacyPaperSourceIdPattern)
    source_kind: Literal["user_pdf", "arxiv_pdf", "local_pdf"]
    original_filename_label: str = Field(min_length=1)
    storage_path_label: str
    source_pdf_sha256: str = Field(pattern=Sha256Pattern)
    size_bytes: int = Field(ge=0)
    page_count: int | None = None
    mime_type: str = Field(min_length=1)
    created_at: datetime

    @field_validator("storage_path_label")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return validate_workspace_path(value)


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------


class PaperClaim(BaseModel):
    """A single claim about the paper with evidence anchoring."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(pattern=IdentifierPattern)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    value: Any
    status: Literal["confirmed", "inferred", "conflicting", "unknown"]
    confidence: Literal["low", "medium", "high"]
    evidence_ids: list[str] = Field(default_factory=list)
    rationale_summary: str | None = None


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


class PaperMentionedCandidate(BaseModel):
    """A candidate element mentioned in the paper (paper_mentioned only)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_id: str = Field(pattern=IdentifierPattern)
    kind: Literal[
        "baseline",
        "dataset",
        "metric",
        "repository",
        "pretrained_model",
        "external_asset",
        "idea_source",
    ]
    name: str = Field(min_length=1)
    normalized_name: str | None = None
    mention_role: Literal[
        "proposed_method",
        "used_in_experiment",
        "compared_baseline",
        "cited_only",
        "dataset_evaluation",
        "metric_reported",
        "implementation_link",
        "supplementary_asset",
        "unclear",
    ]
    selection_status: Literal["paper_mentioned"]
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PaperIdeaSourceCandidate(BaseModel):
    """A research idea candidate derived from the paper (not confirmed)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    idea_source_id: str = Field(pattern=IdentifierPattern)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    suggested_transfer_surface: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    selection_status: Literal["paper_derived_unconfirmed"]


class RepositoryLinkCandidate(BaseModel):
    """A repository URL candidate found in the paper."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    link_id: str = Field(pattern=IdentifierPattern)
    url: str = Field(min_length=1)
    source: Literal["paper_body", "footnote", "project_page", "web_context"]
    evidence_ids: list[str] = Field(default_factory=list)
    selection_status: Literal["paper_mentioned"]
    handoff_target: Literal["repository_discovery"]


# ---------------------------------------------------------------------------
# Method Components
# ---------------------------------------------------------------------------


class MethodComponent(BaseModel):
    """One method component decomposed from the paper."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    component_id: str = Field(pattern=IdentifierPattern)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    input_signal: str | None = None
    output_signal: str | None = None
    training_required: bool | None = None
    inference_required: bool | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["confirmed", "inferred", "conflicting", "unknown"]


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class PaperSummary(BaseModel):
    """Structured paper understanding summary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    source_id: str = Field(pattern=IdentifierPattern)
    title: PaperClaim
    research_problem: list[PaperClaim] = Field(default_factory=list)
    proposed_method: list[PaperClaim] = Field(default_factory=list)
    core_components: list[PaperClaim] = Field(default_factory=list)
    training_objective: list[PaperClaim] = Field(default_factory=list)
    data_assumptions: list[PaperClaim] = Field(default_factory=list)
    label_assumptions: list[PaperClaim] = Field(default_factory=list)
    inference_procedure: list[PaperClaim] = Field(default_factory=list)
    contributions: list[PaperClaim] = Field(default_factory=list)
    stated_limitations: list[PaperClaim] = Field(default_factory=list)
    potential_transfer_points: list[PaperClaim] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


class PaperReaderResult(BaseModel):
    """Final result of the Paper Intelligence Capability."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    run_id: str = Field(pattern=IdentifierPattern)
    status: Literal["success", "partial_success", "failed"]
    paper_summary_path: str | None = None
    method_components_path: str | None = None
    paper_candidates_path: str | None = None
    paper_uncertainties_path: str | None = None
    paper_idea_sources_path: str | None = None
    repository_link_candidates_path: str | None = None
    validation_report_path: str | None = None
    warnings: list[str] = Field(default_factory=list)
