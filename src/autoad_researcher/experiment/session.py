"""Durable control-plane state for one confirmed experiment task."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ExecutionMode = Literal[
    "plan_only",
    "approve_each_step",
    "agent_assisted_after_approval",
]
SessionStatus = Literal[
    "CREATED",
    "ENVIRONMENT_PENDING",
    "ENVIRONMENT_RUNNING",
    "ENVIRONMENT_FAILED",
    "READY_FOR_BASELINE",
    "BASELINE_RUNNING",
    "READY",
    "FAILED",
    "CANCELLED",
]
ReadinessStatus = Literal["unresolved", "resolving", "ready", "blocked"]


class ExperimentAuthorization(BaseModel):
    """The current effective user authorization for a Session."""

    model_config = ConfigDict(extra="forbid")

    execution_mode: ExecutionMode
    confirmed_at: str


class ExperimentSession(BaseModel):
    """Persistent authority for the environment and later experiment lifecycle."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    task_ref: str = Field(min_length=1)
    task_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: SessionStatus = "CREATED"
    repository_ref: str | None = None
    execution_repository_binding_ref: str | None = None
    execution_repository_binding_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    environment_status: str = "not_started"
    environment_snapshot_ref: str | None = None
    baseline_status: str = "not_started"
    evaluation_contract_ref: str | None = None
    evaluation_contract_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evaluation_contract_revision: int | None = Field(default=None, ge=0)
    budget: dict[str, Any] = Field(default_factory=dict)
    model_route: dict[str, Any] = Field(default_factory=dict)
    readiness_status: ReadinessStatus = "unresolved"
    readiness_blockers: list[str] = Field(default_factory=list)
    environment_revision: int = Field(default=0, ge=0)
    authorization: ExperimentAuthorization
    authorization_revision: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_evaluation_contract_ref(self):
        if (self.execution_repository_binding_ref is None) != (self.execution_repository_binding_sha256 is None):
            raise ValueError("execution repository binding reference and SHA-256 must be configured together")
        if (self.evaluation_contract_ref is None) != (self.evaluation_contract_sha256 is None):
            raise ValueError("evaluation contract reference and SHA-256 must be configured together")
        if self.evaluation_contract_ref is None and self.evaluation_contract_revision is not None:
            raise ValueError("evaluation contract revision requires a frozen contract reference")
        return self
