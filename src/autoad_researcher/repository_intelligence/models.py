"""Core Repository Intelligence contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoad_researcher.repository_intelligence.evidence_models import EvidenceRef
from autoad_researcher.repository_intelligence.ids import (
    GitCommitPattern,
    IdentifierPattern,
    Sha256Pattern,
    validate_relative_path,
)
from autoad_researcher.repository_intelligence.status import ClaimStatus, Confidence, RepositoryIntelligenceStatus


class RepositoryAgentBudget(BaseModel):
    """Repository Agent budget with separate analysis and repair reserves."""

    model_config = ConfigDict(extra="forbid")

    max_total_tool_calls: int = Field(ge=0)
    max_total_llm_calls: int = Field(ge=0)
    max_total_input_tokens: int = Field(ge=0)
    max_total_output_tokens: int = Field(ge=0)

    max_discovery_search_calls: int = Field(ge=0)
    max_discovery_fetch_calls: int = Field(ge=0)

    max_analysis_tool_calls: int = Field(ge=0)
    max_analysis_file_reads: int = Field(ge=0)
    max_analysis_search_calls: int = Field(ge=0)
    max_analysis_llm_calls: int = Field(ge=0)

    max_repair_tool_calls: int = Field(ge=0)
    max_repair_llm_calls: int = Field(ge=0)
    max_repairs: int = Field(ge=0)

    max_no_progress_cycles: int = Field(default=2, ge=0)


class RepositoryIntelligenceRequest(BaseModel):
    """Input contract for Repository Intelligence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    request_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    user_goal: str = Field(min_length=1)

    repository_url: str | None = None
    local_path: str | None = None
    requested_ref: str | None = None

    paper_title: str | None = None
    paper_url: str | None = None
    project_name: str | None = None
    method_name: str | None = None
    authors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    discovery_allowed: bool
    user_confirmation_policy: Literal["always", "when_ambiguous", "never"]
    budget_profile: Literal["small", "medium", "large", "custom"]
    budget: RepositoryAgentBudget | None = None

    @model_validator(mode="after")
    def _validate_custom_budget(self):
        if self.budget_profile == "custom" and self.budget is None:
            raise ValueError("custom budget_profile requires budget")
        return self


class RepositoryCandidate(BaseModel):
    """One repository candidate discovered or supplied by the user."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_id: str = Field(pattern=IdentifierPattern)
    canonical_url: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    default_branch: str | None = None
    requested_ref: str | None = None
    resolved_commit: str | None = Field(default=None, pattern=GitCommitPattern)

    official_link_found: bool
    author_or_org_match: bool
    paper_reference_found: bool
    method_name_match: Literal["none", "weak", "strong"]
    is_fork: bool
    is_archived: bool

    confidence: Confidence
    selection_rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RepositoryResolution(BaseModel):
    """Selected repository resolution and confirmation state."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    status: Literal["resolved", "needs_user_confirmation", "not_found", "blocked"]
    selected_candidate_id: str | None = Field(default=None, pattern=IdentifierPattern)
    alternative_candidate_ids: list[str] = Field(default_factory=list)
    resolved_ref: str | None = None
    resolved_commit: str | None = Field(default=None, pattern=GitCommitPattern)
    resolution_reason: str = Field(min_length=1)
    user_confirmation_required: bool
    user_decision: str | None = None

    @model_validator(mode="after")
    def _validate_resolution(self):
        if self.status == "resolved" and self.selected_candidate_id is None:
            raise ValueError("resolved status requires selected_candidate_id")
        if self.status == "resolved" and self.resolved_commit is None:
            raise ValueError("resolved status requires resolved_commit")
        if self.user_confirmation_required and self.status != "needs_user_confirmation":
            raise ValueError("user_confirmation_required requires needs_user_confirmation status")
        return self


class SubmoduleRecord(BaseModel):
    """Raw .gitmodules declaration placeholder.

    The 3.1 plan names SubmoduleRecord but does not seal its inner fields yet.
    Keep parsed declarations structured without inventing required keys.
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class RepositorySource(BaseModel):
    """Acquired or local repository source identity."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    source_id: str = Field(pattern=IdentifierPattern)
    kind: Literal["github_public", "local_workspace"]
    canonical_remote_url: str | None
    requested_ref: str | None
    acquisition_profile: Literal["shallow_ref", "partial_exact", "generic_shallow", "local"]
    resolved_commit: str | None = Field(default=None, pattern=GitCommitPattern)
    tree_sha: str = Field(pattern=Sha256Pattern)
    detached_head: bool | None
    dirty: bool
    local_path_label: str
    submodule_declarations: list[SubmoduleRecord] = Field(default_factory=list)
    source_fingerprint: str = Field(pattern=Sha256Pattern)

    @field_validator("local_path_label")
    @classmethod
    def _validate_local_path_label(cls, value: str) -> str:
        return validate_relative_path(value)


class RepositoryClaim(BaseModel):
    """One evidence-backed repository claim."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(pattern=IdentifierPattern)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    value: Any
    status: ClaimStatus
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list)
    rationale_summary: str | None = None

    @model_validator(mode="after")
    def _validate_claim_evidence(self):
        if self.status == "confirmed" and not self.evidence_ids:
            raise ValueError("confirmed claim requires evidence_ids")
        if self.status == "inferred" and not self.rationale_summary:
            raise ValueError("inferred claim requires rationale_summary")
        return self


class RepositoryArtifactPaths(BaseModel):
    """The seven formal Repository Intelligence artifact paths."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repository_summary: str
    entrypoints: str
    dependency_evidence: str
    modifiable_paths: str
    evaluation_contract_draft: str
    environment_context: str
    uncertainties: str

    @field_validator(
        "repository_summary",
        "entrypoints",
        "dependency_evidence",
        "modifiable_paths",
        "evaluation_contract_draft",
        "environment_context",
        "uncertainties",
    )
    @classmethod
    def _validate_artifact_path(cls, value: str) -> str:
        return validate_relative_path(value)

    def path_set(self) -> set[str]:
        return set(self.model_dump(mode="json").values())


class RepositoryIntelligenceResult(BaseModel):
    """Final result envelope for Repository Intelligence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    request_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    status: RepositoryIntelligenceStatus
    source_id: str | None = Field(default=None, pattern=IdentifierPattern)
    artifacts: RepositoryArtifactPaths
    artifact_sha256: dict[str, str]
    evidence_index_path: str
    evidence_index_sha256: str = Field(pattern=Sha256Pattern)
    validation_report_path: str
    validation_report_sha256: str = Field(pattern=Sha256Pattern)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("evidence_index_path", "validation_report_path")
    @classmethod
    def _validate_result_path(cls, value: str) -> str:
        return validate_relative_path(value)

    @model_validator(mode="after")
    def _validate_artifact_references(self):
        formal_paths = self.artifacts.path_set()
        sha_paths = set(self.artifact_sha256)
        missing = formal_paths - sha_paths
        extra = sha_paths - formal_paths
        if missing:
            raise ValueError(f"artifact_sha256 missing formal artifact paths: {sorted(missing)}")
        if extra:
            raise ValueError(f"artifact_sha256 contains non-formal artifact paths: {sorted(extra)}")
        for path, sha in self.artifact_sha256.items():
            validate_relative_path(path)
            if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError(f"artifact_sha256 value must be sha256 hex for {path!r}")
        return self
