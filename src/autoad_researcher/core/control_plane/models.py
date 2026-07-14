"""Strict persisted models for canonical events and pipeline jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ControlPlaneEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int = Field(ge=1)
    type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    idempotency_key: str | None = None
    payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class PipelineJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=r"^job_[0-9]{6}$")
    source_id: str
    job_type: str = Field(min_length=1)
    status: Literal["queued", "running", "completed", "failed"]
    evidence_role: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    outputs: list[str] = Field(default_factory=list)
    error: Any | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    request_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attempt_count: int = Field(default=0, ge=0)
    claimed_by: str | None = None
    claim_token: str | None = None
    attempt_started_at: datetime | None = None
    lease_expires_at: datetime | None = None
    consecutive_stale_count: int = Field(default=0, ge=0)
    consecutive_lease_expiry_count: int = Field(default=0, ge=0)
    next_eligible_at: datetime | None = None
    pending_control_request_id: str | None = None
    active_control_request_id: str | None = None


class JobTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    from_status: Literal["queued", "running", "completed", "failed"]
    to_status: Literal["queued", "running", "completed", "failed"]
    reason: str
    attempt_count: int = Field(ge=0)


class ClaimRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    job_id: str
    attempt_count: int = Field(ge=1)
    claim_token: str
    worker_id: str
    claimed_at: datetime
    lease_expires_at: datetime | None = None
    control_request_id: str | None = None


class AttemptResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    job_id: str
    attempt_count: int = Field(ge=1)
    claim_token: str
    worker_id: str
    status: Literal[
        "claim_aborted",
        "completed",
        "published",
        "no_op",
        "failed",
        "lease_lost",
        "stale_input",
    ]
    control_request_id: str | None = None
    input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    publication_check_input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    canonical_readiness_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    outputs: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime
    error: str | None = None


FactStatus = Literal[
    "verified",
    "unverified",
    "missing",
    "unavailable_due_to_dependency",
    "not_applicable",
    "conflict",
]


class ReadinessEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_path: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ReadinessFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    status: FactStatus
    value: Any | None = None
    evidence: list[ReadinessEvidenceRef] = Field(default_factory=list)
    detail: str | None = None


class ResolverSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolver_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    layers: list[Literal["implementation", "execution"]] = Field(min_length=1)
    observed_inputs: list[ReadinessEvidenceRef] = Field(default_factory=list)
    facts: list[ReadinessFact] = Field(default_factory=list)


class MaterializationInputSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    materializer_version: Literal["experiment_readiness:v1"] = "experiment_readiness:v1"
    fact_policy_version: Literal["readiness_fact_policy:v2"] = "readiness_fact_policy:v2"
    resolver_schema_versions: dict[str, str] = Field(default_factory=dict)
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    components: dict[str, ResolverSnapshot] = Field(default_factory=dict)


class ReadinessLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer: Literal["planning", "implementation", "execution"]
    ready: bool
    facts: list[ReadinessFact] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)


class ExecutionAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorized: bool = False
    execution_mode: Literal["plan_only", "approve_each_step", "agent_assisted_after_approval"]
    reason: str


class ExperimentReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    revision: int = Field(ge=1)
    session_id: str = Field(min_length=1)
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    materialization_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    planning_readiness: ReadinessLayer
    implementation_readiness: ReadinessLayer
    execution_readiness: ReadinessLayer
    execution_authorization: ExecutionAuthorization
    materialized_at: datetime


class ExperimentSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepare_job_id: str = Field(pattern=r"^job_[0-9]{6}$")
    status: Literal["queued", "preparing", "materialized", "failed"]
    readiness_path: str = "experiment_agents/readiness.json"
    created_at: datetime
    updated_at: datetime
    error: str | None = None


class MaterializationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["published", "no_op", "stale_input"]
    job_status: Literal["queued", "completed", "failed"]
    readiness_path: str | None = None
    materialization_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_check_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContractConfirmationProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    confirmation_id: str = Field(min_length=1)
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "needs_clarification", "superseded", "rejected", "confirmed"]
    decision: Literal["approved", "rejected"] | None = None
    contract_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    requested_at: datetime
    resolved_at: datetime | None = None
    inconsistency: str | None = None
    audit_repair_required: bool = False
    lifecycle_revision: int = Field(default=0, ge=0)


class MaterializationRequestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str = Field(min_length=1)
    force: bool
    reason: str = Field(min_length=1)
    action: Literal["scheduled", "not_scheduled"]
    status: Literal["scheduled", "not_scheduled", "completed", "failed"]
    executed: bool
    active_job_id: str = Field(pattern=r"^job_[0-9]{6}$")
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
