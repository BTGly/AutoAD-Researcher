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
