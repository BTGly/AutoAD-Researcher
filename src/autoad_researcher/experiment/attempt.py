"""Durable runtime state for one queued experiment attempt."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.runner.models import ExperimentCommandPlan, ExperimentInputRefs

AttemptPurpose = Literal[
    "baseline",
    "exploration",
    "confirmation",
    "noise_calibration",
    "repair",
]
AttemptRuntimeStatus = Literal[
    "QUEUED",
    "STARTING",
    "RUNNING",
    "TERMINATING",
    "COMPLETED",
    "FAILED",
    "TIMED_OUT",
    "CANCELLED",
    "LOST",
]
AttemptJobType = Literal[
    "experiment_baseline",
    "experiment_attempt",
    "experiment_confirmatory",
]


class ExperimentAttempt(BaseModel):
    """The authority for an experiment process, separate from PipelineJob."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    attempt_id: str = Field(pattern=r"^attempt_[0-9]{6}$")
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    job_type: AttemptJobType
    pipeline_job_id: str | None = Field(default=None, pattern=r"^job_[0-9]{6}$")
    attempt_purpose: AttemptPurpose
    command_plan: ExperimentCommandPlan
    input_refs: ExperimentInputRefs
    required_device_count: int = Field(default=0, ge=0)
    required_vram_mb: int = Field(default=0, ge=0)
    resource_lease_id: str | None = Field(default=None, pattern=r"^lease_[0-9]{6}$")
    runtime_status: AttemptRuntimeStatus = "QUEUED"
    pid: int | None = Field(default=None, gt=0)
    process_group_id: int | None = Field(default=None, gt=0)
    heartbeat_at: str | None = None
    cancel_requested_at: str | None = None
    termination_requested_at: str | None = None
    termination_reason: str | None = None
    termination_grace_seconds: int = Field(default=30, gt=0)
    checkpoint_watch_path: str | None = None
    checkpoint_stall_seconds: int | None = Field(default=None, gt=0)
    job_timeout_sec: int = Field(gt=0)
    retry_of: str | None = Field(default=None, pattern=r"^attempt_[0-9]{6}$")
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=0, ge=0)
    retry_not_before: str | None = None
    failure_code: str | None = None
    retry_exhausted: bool = False
    execution_result_ref: str | None = None
    evaluation_contract_ref: str | None = None
    evaluation_contract_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    protected_artifact_report_ref: str | None = None
    protected_artifact_report_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_at: str
    updated_at: str
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_retry_lineage(self):
        if self.retry_of is None and self.retry_count != 0:
            raise ValueError("root Attempt must have retry_count=0")
        if self.retry_of is not None and self.retry_count < 1:
            raise ValueError("retried Attempt must have retry_count >= 1")
        if self.retry_count > self.max_retries:
            raise ValueError("retry_count must not exceed max_retries")
        if self.retry_exhausted and self.runtime_status not in {"FAILED", "TIMED_OUT", "LOST"}:
            raise ValueError("retry_exhausted requires a terminal failed runtime status")
        if self.resource_lease_id is not None and self.required_device_count == 0:
            raise ValueError("ResourceLease requires a positive device request")
        if self.checkpoint_watch_path is not None:
            checkpoint_path = PurePosixPath(self.checkpoint_watch_path)
            if checkpoint_path.is_absolute() or any(part == ".." for part in checkpoint_path.parts):
                raise ValueError("checkpoint_watch_path must stay within the Attempt artifact directory")
        if (self.checkpoint_watch_path is None) != (self.checkpoint_stall_seconds is None):
            raise ValueError("checkpoint watch path and stall interval must be configured together")
        return self
