"""Step 3.8: Experiment Execution — sealed schemas v2.12.

All models match the sealed contract in docs/3.8开发计划.md.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2

# ── Enums ─────────────────────────────────────────────────────────────────────


class ExecutionUnitStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


TerminalReason = Literal[
    "completed",
    "execution_failed",
    "validity_failed",
    "insufficient_evidence",
    "blocked_upstream_failure",
    "intake_failed",
    "preflight_failed",
]

ExecutionStatus = Literal["succeeded", "failed", "timeout", "not_run"]

FailureClassification = Literal[
    "max_retries", "wall_time", "metric", "environment", "repository",
]

OverallExecutionStatus = Literal["completed", "partially_completed", "failed", "blocked"]

# ── Identity ──────────────────────────────────────────────────────────────────


class AttemptIdentitySnapshot(BaseModel):
    """Immutable identity context for one attempt.

    Four canonical fields determine whether a retry is the same logical
    operation or a different one.
    """

    model_config = ConfigDict(extra="forbid")

    execution_unit_plan_sha256: str = Field(pattern=Sha256Pattern)
    command_sha256: str = Field(pattern=Sha256Pattern)
    input_refs_sha256: str = Field(pattern=Sha256Pattern)
    workspace_repository_fingerprint: str = Field(min_length=1)


class RetryIdentity(BaseModel):
    """Identity fields that must match for a retry of the same command."""

    model_config = ConfigDict(extra="forbid")

    execution_unit_plan_sha256: str = Field(pattern=Sha256Pattern)
    command_sha256: str = Field(pattern=Sha256Pattern)
    input_refs_sha256: str = Field(pattern=Sha256Pattern)
    workspace_repository_fingerprint: str = Field(min_length=1)


# ── Workspace ─────────────────────────────────────────────────────────────────


class WorkspaceExecutionRef(BaseModel):
    """Reference to a single workspace that will be executed.

    Baseline workspaces carry no patch/variant fields.
    Variant workspaces must carry all four workspace-specific fields.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(pattern=IdentifierPattern)
    subject_type: Literal["baseline", "variant"]
    variant_ids: list[str] = Field(default_factory=list)
    repository_fingerprint: str = Field(min_length=1)
    repository_commit: str = Field(min_length=1)
    patch_diff_sha256: str | None = None
    local_validation_report_sha256: str | None = None
    patch_application_manifest_ref: ArtifactReferenceV2 | None = None
    post_patch_validation_report_ref: ArtifactReferenceV2 | None = None

    @model_validator(mode="after")
    def _validate(self) -> "WorkspaceExecutionRef":
        if self.subject_type == "baseline":
            if self.variant_ids:
                raise ValueError("baseline workspace must have empty variant_ids")
            if self.patch_diff_sha256 is not None:
                raise ValueError("baseline workspace must have patch_diff_sha256=None")
            if self.local_validation_report_sha256 is not None:
                raise ValueError("baseline workspace must have local_validation_report_sha256=None")
            if self.patch_application_manifest_ref is not None:
                raise ValueError("baseline workspace must have patch_application_manifest_ref=None")
            if self.post_patch_validation_report_ref is not None:
                raise ValueError("baseline workspace must have post_patch_validation_report_ref=None")
        else:
            if not self.variant_ids:
                raise ValueError("variant workspace must have non-empty variant_ids")
            if self.patch_diff_sha256 is None:
                raise ValueError("variant workspace must have patch_diff_sha256")
            if self.local_validation_report_sha256 is None:
                raise ValueError("variant workspace must have local_validation_report_sha256")
            if self.patch_application_manifest_ref is None:
                raise ValueError("variant workspace must have patch_application_manifest_ref")
            if self.post_patch_validation_report_ref is None:
                raise ValueError("variant workspace must have post_patch_validation_report_ref")
        return self


# ── Intake ────────────────────────────────────────────────────────────────────


class IntakeCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    status: Literal["passed", "failed"]
    details: str | None = None


class RunnerIntakeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overall: Literal["passed", "failed"]
    checks: list[IntakeCheck] = Field(default_factory=list)


# ── Artifact bindings ─────────────────────────────────────────────────────────


class PlannedArtifactBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding_id: str = Field(pattern=IdentifierPattern)
    role: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    producing_unit_id: str = Field(pattern=IdentifierPattern)


class PlannedArtifactProduction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str = Field(pattern=IdentifierPattern)
    bindings: list[PlannedArtifactBinding] = Field(default_factory=list)


class ResolvedArtifactBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding_id: str = Field(pattern=IdentifierPattern)
    role: str = Field(min_length=1)
    artifact_ref: ArtifactReferenceV2
    artifact_sha256: str = Field(pattern=Sha256Pattern)


class ProducedArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str = Field(pattern=IdentifierPattern)
    attempt_id: str = Field(pattern=IdentifierPattern)
    bindings: list[ResolvedArtifactBinding] = Field(default_factory=list)


# ── Plan ──────────────────────────────────────────────────────────────────────


class ExecutionUnitPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str = Field(pattern=IdentifierPattern)
    matrix_entry_id: str = Field(pattern=IdentifierPattern)
    variant_id: str | None = None
    seed: int | None = None
    workspace_id: str = Field(pattern=IdentifierPattern)
    stage: str = Field(min_length=1)
    command_plan_sha256: str = Field(pattern=Sha256Pattern)
    planned_productions: list[PlannedArtifactProduction] = Field(default_factory=list)
    max_attempts: int = Field(ge=1, default=3)
    max_wall_time_seconds: int = Field(ge=1)


# ── Outcome ───────────────────────────────────────────────────────────────────


class AttemptOutcome(BaseModel):
    """Outcome derived from artifact content — not an independent writable fact."""

    model_config = ConfigDict(extra="forbid")

    execution_status: ExecutionStatus
    metrics_status: Literal["passed", "failed", "not_run"]
    validity_status: Literal["valid", "invalid", "insufficient_evidence", "not_run"]

    @model_validator(mode="after")
    def _validate(self) -> "AttemptOutcome":
        if self.execution_status == "succeeded":
            if self.metrics_status == "not_run":
                raise ValueError("succeeded execution requires metrics_status")
            if self.validity_status == "not_run":
                raise ValueError("succeeded execution requires validity_status")
        return self


# ── Resource ───────────────────────────────────────────────────────────────────


class ResourceUsageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(pattern=IdentifierPattern)
    unit_id: str = Field(pattern=IdentifierPattern)
    subject_type: Literal["baseline", "variant"]
    variant_id: str | None = None
    seed: int | None = None
    gpu_count_used: int = Field(ge=0)
    wall_time_seconds: float = Field(ge=0)
    memory_peak_bytes: int | None = Field(default=None, ge=0)
    storage_peak_bytes: int | None = Field(default=None, ge=0)

    @computed_field
    @property
    def actual_gpu_hours(self) -> float:
        return self.gpu_count_used * self.wall_time_seconds / 3600.0

    @model_validator(mode="after")
    def _validate_subject_fields(self) -> "ResourceUsageReport":
        if self.subject_type == "baseline":
            if self.variant_id is not None:
                raise ValueError("baseline must have variant_id=None")
        else:
            if self.variant_id is None:
                raise ValueError("variant must have variant_id")
        return self


# ── Attempt Record ─────────────────────────────────────────────────────────────


class AttemptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(pattern=IdentifierPattern)
    attempt_index: int = Field(ge=1)
    unit_id: str = Field(pattern=IdentifierPattern)
    identity: AttemptIdentitySnapshot
    outcome: AttemptOutcome
    execution_result_ref: ArtifactReferenceV2 | None = None
    metrics_report_ref: ArtifactReferenceV2 | None = None
    validity_report_ref: ArtifactReferenceV2 | None = None
    resource_usage_ref: ArtifactReferenceV2 | None = None
    resolved_bindings: list[ResolvedArtifactBinding] = Field(default_factory=list)
    produced_artifacts: list[ProducedArtifactRecord] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None


# ── Retry ─────────────────────────────────────────────────────────────────────


class RetryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(pattern=IdentifierPattern)
    unit_id: str = Field(pattern=IdentifierPattern)
    prev_identity: RetryIdentity
    identity_match: bool
    decision: Literal[
        "retry_same_command",
        "do_not_retry",
        "return_to_3_5",
        "return_to_3_6_3_7",
        "blocked",
    ]
    failure_classification: FailureClassification
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate(self) -> "RetryDecision":
        if self.decision == "retry_same_command" and not self.identity_match:
            raise ValueError("retry_same_command requires identity_match=True")
        if self.decision != "retry_same_command" and self.identity_match:
            raise ValueError("non-retry decision requires identity_match=False")
        return self


# ── Unit Record ───────────────────────────────────────────────────────────────


class ExecutionUnitRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(pattern=IdentifierPattern)
    matrix_entry_id: str = Field(pattern=IdentifierPattern)
    variant_id: str | None = None
    seed: int | None = None
    stage: str = Field(min_length=1)
    workspace_id: str = Field(pattern=IdentifierPattern)
    final_status: ExecutionUnitStatus
    final_attempt_id: str | None = None
    attempts: list[AttemptRecord] = Field(default_factory=list)
    terminal_reason: TerminalReason
    blocking_unit_ids: list[str] = Field(default_factory=list)
    preflight_report_ref: ArtifactReferenceV2 | None = None

    @model_validator(mode="after")
    def _validate(self) -> "ExecutionUnitRecord":
        if self.terminal_reason in (
            "completed", "execution_failed", "validity_failed", "insufficient_evidence",
        ):
            if not self.attempts:
                raise ValueError(f"{self.terminal_reason} requires at least one attempt")
            if any(a.unit_id != self.unit_id for a in self.attempts):
                raise ValueError("attempt belongs to a different execution unit")
            if self.final_attempt_id is None:
                raise ValueError("final_attempt_id must be set")
            attempt_ids = [a.attempt_id for a in self.attempts]
            if len(attempt_ids) != len(set(attempt_ids)):
                raise ValueError("duplicate attempt_id in attempts")
            attempt_indices = [a.attempt_index for a in self.attempts]
            if attempt_indices[0] != 1:
                raise ValueError("attempt_index must start at 1")
            if attempt_indices != sorted(attempt_indices):
                raise ValueError("attempt_index must be strictly increasing")
            if len(attempt_indices) != len(set(attempt_indices)):
                raise ValueError("duplicate attempt_index in attempts")
            plan_shas = {a.identity.execution_unit_plan_sha256 for a in self.attempts}
            if len(plan_shas) != 1:
                raise ValueError("all attempts in a unit must share execution_unit_plan_sha256")
            last = self.attempts[-1]
            if self.final_attempt_id != last.attempt_id:
                raise ValueError("final_attempt_id must match last attempt")
            if self.blocking_unit_ids:
                raise ValueError("attemptful terminal_reason must have empty blocking_unit_ids")
            if self.preflight_report_ref is not None:
                raise ValueError("attemptful terminal_reason must have preflight_report_ref=None")
        elif self.terminal_reason == "blocked_upstream_failure":
            if self.attempts:
                raise ValueError("blocked_upstream_failure requires zero attempts")
            if self.final_attempt_id is not None:
                raise ValueError("blocked_upstream_failure requires final_attempt_id=None")
            if not self.blocking_unit_ids:
                raise ValueError("blocked_upstream_failure requires blocking_unit_ids")
            if self.final_status != ExecutionUnitStatus.BLOCKED:
                raise ValueError("blocked_upstream_failure requires final_status=BLOCKED")
        elif self.terminal_reason == "preflight_failed":
            if self.attempts:
                raise ValueError("preflight_failed requires zero attempts")
            if self.final_attempt_id is not None:
                raise ValueError("preflight_failed requires final_attempt_id=None")
            if self.preflight_report_ref is None:
                raise ValueError("preflight_failed requires preflight_report_ref")
            if self.blocking_unit_ids:
                raise ValueError("preflight_failed must have empty blocking_unit_ids")
            if self.final_status != ExecutionUnitStatus.BLOCKED:
                raise ValueError("preflight_failed requires final_status=BLOCKED")
        elif self.terminal_reason == "intake_failed":
            if self.attempts:
                raise ValueError("intake_failed requires zero attempts")
            if self.final_attempt_id is not None:
                raise ValueError("intake_failed requires final_attempt_id=None")
            if self.preflight_report_ref is not None:
                raise ValueError("intake_failed must have preflight_report_ref=None")
            if self.blocking_unit_ids:
                raise ValueError("intake_failed must have empty blocking_unit_ids")
            if self.final_status != ExecutionUnitStatus.BLOCKED:
                raise ValueError("intake_failed requires final_status=BLOCKED")
        return self


# ── Matrix Coverage ───────────────────────────────────────────────────────────


class MatrixCoverageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_unit_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)


# ── Manifest ──────────────────────────────────────────────────────────────────


class ExecutionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=IdentifierPattern)
    experiment_matrix_sha256: str = Field(pattern=Sha256Pattern)
    protocol_fingerprint: str = Field(min_length=1)
    workspace_refs_sha256: str = Field(pattern=Sha256Pattern)
    operational_guard_policy_sha256: str = Field(pattern=Sha256Pattern)
    runner_intake_report_ref: ArtifactReferenceV2
    unit_records: list[ExecutionUnitRecord] = Field(default_factory=list)
    completed_unit_count: int = Field(ge=0)
    failed_unit_count: int = Field(ge=0)
    blocked_unit_count: int = Field(ge=0)
    retry_decisions: list[RetryDecision] = Field(default_factory=list)
    matrix_coverage: MatrixCoverageReport | None = None

    @model_validator(mode="after")
    def _counts_match_records(self) -> "ExecutionManifest":
        completed = sum(1 for r in self.unit_records if r.final_status == ExecutionUnitStatus.COMPLETED)
        failed = sum(1 for r in self.unit_records if r.final_status == ExecutionUnitStatus.FAILED)
        blocked = sum(1 for r in self.unit_records if r.final_status == ExecutionUnitStatus.BLOCKED)
        if self.completed_unit_count != completed:
            raise ValueError(
                f"completed_unit_count={self.completed_unit_count} != derived={completed}"
            )
        if self.failed_unit_count != failed:
            raise ValueError(
                f"failed_unit_count={self.failed_unit_count} != derived={failed}"
            )
        if self.blocked_unit_count != blocked:
            raise ValueError(
                f"blocked_unit_count={self.blocked_unit_count} != derived={blocked}"
            )
        return self


# ── Handoff ───────────────────────────────────────────────────────────────────


class ExperimentExecutionHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=IdentifierPattern)
    execution_manifest_ref: ArtifactReferenceV2
    execution_unit_plans_sha256: str = Field(pattern=Sha256Pattern)
    experiment_matrix_sha256: str = Field(pattern=Sha256Pattern)
    statistical_analysis_plan_sha256: str = Field(pattern=Sha256Pattern)
    protocol_fingerprint: str = Field(min_length=1)
    runner_intake_report_ref: ArtifactReferenceV2
    resource_budget_ref: ArtifactReferenceV2
    budget_decision_ref: ArtifactReferenceV2
    workspace_refs: list[WorkspaceExecutionRef] = Field(default_factory=list)
    completed_unit_ids: list[str] = Field(default_factory=list)
    failed_unit_ids: list[str] = Field(default_factory=list)
    blocked_unit_ids: list[str] = Field(default_factory=list)
    overall_status: OverallExecutionStatus
    next_stage: Literal["3.9_results_analysis"] = "3.9_results_analysis"

    @model_validator(mode="after")
    def _id_sets_disjoint(self) -> "ExperimentExecutionHandoff":
        completed = set(self.completed_unit_ids)
        failed = set(self.failed_unit_ids)
        blocked = set(self.blocked_unit_ids)
        if completed & failed or failed & blocked or completed & blocked:
            raise ValueError("unit ID sets must be disjoint")
        return self


# ── Resource Ledger ───────────────────────────────────────────────────────────


class ExecutionUnitResourceLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str = Field(pattern=IdentifierPattern)
    resource_reports: list[ArtifactReferenceV2] = Field(default_factory=list)
    total_wall_time: float = Field(ge=0)
    total_gpu_hours: float = Field(ge=0)
