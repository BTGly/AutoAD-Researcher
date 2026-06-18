"""Step 3.8: Experiment Execution — sealed run-time schemas.

All models in this module are sealed and should not be modified without
updating the 3.8 contract boundary.
"""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2, ResolvedArtifact

# ── Type aliases & enums ──────────────────────────────────────────────────────

FailureClassification = Literal[
    "max_retries",
    "wall_time",
    "metric",
    "environment",
    "repository",
]

ExecutionUnitStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped",
]

TerminalReason = (
    Literal[
        "max_retries_exceeded",
        "total_wall_time_exceeded",
        "terminal_metric_failure",
        "terminal_environment_error",
        "terminal_invalid_repository",
    ]
    | None
)

# ── Intake layer ──────────────────────────────────────────────────────────────


class WorkspaceExecutionRef(BaseModel):
    """Reference to a single workspace that will be executed."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(pattern=IdentifierPattern)
    variant_ids: list[str] = Field(default_factory=list)


class RunnerIntakeRequest(BaseModel):
    """Structured intake request from Step 3.7 → 3.8 runner."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=IdentifierPattern)
    handoff_ref: ArtifactReferenceV2
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    workspace_execution_refs: list[WorkspaceExecutionRef] = Field(default_factory=list)


class IntakeCheck(BaseModel):
    """One check within a RunnerIntakeReport."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    status: Literal["passed", "failed"]
    details: str | None = None


class RunnerIntakeReport(BaseModel):
    """Result of validating a RunnerIntakeRequest against a PatchRunnerHandoff."""

    model_config = ConfigDict(extra="forbid")

    overall: Literal["passed", "failed"]
    checks: list[IntakeCheck] = Field(default_factory=list)


# ── Planned state ─────────────────────────────────────────────────────────────


class PlannedArtifactBinding(BaseModel):
    """Declares that a planned unit will produce an artifact of a given role/type."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    producing_unit_id: str = Field(pattern=IdentifierPattern)


class PlannedArtifactProduction(BaseModel):
    """One unit's planned artifact bindings."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(pattern=IdentifierPattern)
    bindings: list[PlannedArtifactBinding] = Field(default_factory=list)


class ExecutionUnitPlan(BaseModel):
    """Plan for a single execution unit (one variant set in one workspace)."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(pattern=IdentifierPattern)
    variant_ids: list[str] = Field(default_factory=list)
    workspace_id: str = Field(pattern=IdentifierPattern)
    command_plan: str = Field(min_length=1)
    planned_productions: list[PlannedArtifactProduction] = Field(default_factory=list)
    max_attempts: int = Field(ge=1, default=3)
    max_wall_time_seconds: int = Field(ge=1)


# ── Runtime state ─────────────────────────────────────────────────────────────


class AttemptIdentitySnapshot(BaseModel):
    """Immutable snapshot of the identity context for one attempt."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(pattern=IdentifierPattern)
    attempt_number: int = Field(ge=1)
    repository_fingerprint: str = Field(min_length=1)
    command_sha256: str = Field(pattern=Sha256Pattern)
    environment_sha256: str = Field(pattern=Sha256Pattern)
    dataset_sha256: str = Field(pattern=Sha256Pattern)


class AttemptOutcome(BaseModel):
    """Outcome of a single attempt, with references to execution artifacts."""

    model_config = ConfigDict(extra="forbid")

    identity: AttemptIdentitySnapshot
    execution_result_ref: ArtifactReferenceV2
    metrics_report_ref: ArtifactReferenceV2 | None = None
    validity_report_ref: ArtifactReferenceV2 | None = None
    repro_summary_refs: list[ArtifactReferenceV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_identity_consistency(self) -> "AttemptOutcome":
        return self


class AttemptRecord(BaseModel):
    """Full record of one attempt including identity, plan, outcome, and resources."""

    model_config = ConfigDict(extra="forbid")

    identity: AttemptIdentitySnapshot
    experiment_plan_ref: ArtifactReferenceV2
    outcome: AttemptOutcome
    resource_usage_ref: ArtifactReferenceV2


class ResolvedArtifactBinding(BaseModel):
    """Binding from a role to a concrete resolved artifact reference."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1)
    resolved_ref: ArtifactReferenceV2


class ProducedArtifactRecord(BaseModel):
    """Records which artifacts were produced by a specific attempt in a unit."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(pattern=IdentifierPattern)
    attempt_identity: AttemptIdentitySnapshot
    bindings: list[ResolvedArtifactBinding] = Field(default_factory=list)


class ResourceUsageReport(BaseModel):
    """Resource consumption report for one attempt."""

    model_config = ConfigDict(extra="forbid")

    gpu_count_used: int = Field(ge=0)
    wall_time_seconds: float = Field(ge=0)
    memory_peak_bytes: int | None = Field(default=None, ge=0)
    storage_peak_bytes: int | None = Field(default=None, ge=0)

    @computed_field
    @property
    def actual_gpu_hours(self) -> float:
        return self.gpu_count_used * self.wall_time_seconds / 3600.0


class RetryIdentity(BaseModel):
    """Identifies a specific attempt that triggered a retry decision."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(pattern=IdentifierPattern)
    attempt_number: int = Field(ge=1)
    retry_reason: str = Field(min_length=1)


class RetryDecision(BaseModel):
    """Decision about whether to retry a failed attempt."""

    model_config = ConfigDict(extra="forbid")

    identity: RetryIdentity
    should_retry: bool
    reason: str = Field(min_length=1)
    next_attempt_number: int = Field(ge=1)
    failure_classification: FailureClassification

    @model_validator(mode="after")
    def _validate_retry_decision_consistency(self) -> "RetryDecision":
        if self.should_retry and self.next_attempt_number <= self.identity.attempt_number:
            raise ValueError(
                f"next_attempt_number ({self.next_attempt_number}) must be > "
                f"current attempt_number ({self.identity.attempt_number}) when should_retry=True"
            )
        if not self.should_retry and self.next_attempt_number != self.identity.attempt_number:
            raise ValueError(
                f"next_attempt_number ({self.next_attempt_number}) must equal "
                f"current attempt_number ({self.identity.attempt_number}) when should_retry=False"
            )
        return self


class MatrixCoverageReport(BaseModel):
    """Aggregate report of which execution units succeeded/failed within a matrix."""

    model_config = ConfigDict(extra="forbid")

    unit_records: list = Field(default_factory=list)
    overall_status: ExecutionUnitStatus


# ── Records / Manifest ────────────────────────────────────────────────────────


class ExecutionUnitRecord(BaseModel):
    """Complete record of one execution unit's lifecycle."""

    model_config = ConfigDict(extra="forbid")

    plan: ExecutionUnitPlan
    attempts: list[AttemptRecord] = Field(default_factory=list)
    final_status: ExecutionUnitStatus
    retry_decisions: list[RetryDecision] = Field(default_factory=list)
    resource_ledger_ref: ArtifactReferenceV2 | None = None

    @model_validator(mode="after")
    def _validate_unit_attempt_consistency(self) -> "ExecutionUnitRecord":
        if not self.attempts:
            return self
        unit_id = self.plan.unit_id
        for attempt in self.attempts:
            if attempt.identity.unit_id != unit_id:
                raise ValueError(
                    f"attempt unit_id ({attempt.identity.unit_id}) != "
                    f"plan unit_id ({unit_id})"
                )
        return self


class ExecutionManifest(BaseModel):
    """Top-level manifest for a complete run's execution."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=IdentifierPattern)
    unit_records: list[ExecutionUnitRecord] = Field(default_factory=list)
    handoff_ref: ArtifactReferenceV2 | None = None
    overall_status: ExecutionUnitStatus


class ExperimentExecutionHandoff(BaseModel):
    """Handoff from 3.8 execution to 3.9 analysis, carrying identity snapshots."""

    model_config = ConfigDict(extra="forbid")

    manifest: ExecutionManifest
    identity_snapshots: list[AttemptIdentitySnapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_handoff_consistency(self) -> "ExperimentExecutionHandoff":
        snapshot_keys = {(s.unit_id, s.attempt_number) for s in self.identity_snapshots}
        for unit in self.manifest.unit_records:
            for attempt in unit.attempts:
                key = (attempt.identity.unit_id, attempt.identity.attempt_number)
                if key not in snapshot_keys:
                    raise ValueError(
                        f"attempt ({key[0]}, attempt {key[1]}) missing from identity_snapshots"
                    )
        return self


class ExecutionUnitResourceLedger(BaseModel):
    """Aggregate resource ledger across all attempts of one execution unit."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(pattern=IdentifierPattern)
    resource_reports: list[ArtifactReferenceV2] = Field(default_factory=list)
    total_wall_time: float = Field(ge=0)
    total_gpu_hours: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_ledger_consistency(self) -> "ExecutionUnitResourceLedger":
        return self
