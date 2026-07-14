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
    started_at: datetime
    finished_at: datetime
    error: str | None = None
