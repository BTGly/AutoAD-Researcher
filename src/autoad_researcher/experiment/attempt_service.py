"""Idempotent control-plane bridge for durable ExperimentAttempt Jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.job_service import create_or_get_pipeline_job
from autoad_researcher.experiment.attempt import AttemptJobType, AttemptPurpose, ExperimentAttempt
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.runner.models import ExperimentCommandPlan, ExperimentInputRefs

ATTEMPT_PURPOSE_BY_JOB_TYPE: dict[AttemptJobType, AttemptPurpose] = {
    "experiment_baseline": "baseline",
    "experiment_attempt": "exploration",
    "experiment_confirmatory": "confirmation",
}


class ExperimentAttemptStartResult(BaseModel):
    """The immutable Attempt plus its one queue Job."""

    model_config = ConfigDict(extra="forbid")

    attempt: ExperimentAttempt
    pipeline_job: dict[str, object]
    disposition: str


class ExperimentAttemptService:
    """Create or repair an Attempt/Job pair without duplicating work."""

    def __init__(
        self,
        *,
        attempt_store: ExperimentAttemptStore | None = None,
        session_store: ExperimentSessionStore | None = None,
    ):
        self._attempt_store = attempt_store or ExperimentAttemptStore()
        self._session_store = session_store or ExperimentSessionStore()

    def create_or_get_attempt(
        self,
        run_dir: Path,
        *,
        session_id: str,
        job_type: AttemptJobType,
        idempotency_key: str,
        command_plan: ExperimentCommandPlan,
        input_refs: ExperimentInputRefs,
        job_timeout_sec: int,
        max_retries: int = 0,
        required_device_count: int = 0,
        required_vram_mb: int = 0,
        termination_grace_seconds: int = 30,
        checkpoint_watch_path: str | None = None,
        checkpoint_stall_seconds: int | None = None,
        evaluation_contract_ref: str | None = None,
        evaluation_contract_sha256: str | None = None,
        protected_artifact_report_ref: str | None = None,
        protected_artifact_report_sha256: str | None = None,
    ) -> ExperimentAttemptStartResult:
        session = self._require_executable_session(run_dir, session_id, job_type)
        now = _utc_now()
        candidate = ExperimentAttempt(
            attempt_id="attempt_000000",
            run_id=run_dir.name,
            session_id=session.session_id,
            idempotency_key=idempotency_key,
            job_type=job_type,
            attempt_purpose=ATTEMPT_PURPOSE_BY_JOB_TYPE[job_type],
            command_plan=command_plan,
            input_refs=input_refs,
            job_timeout_sec=job_timeout_sec,
            max_retries=max_retries,
            required_device_count=required_device_count,
            required_vram_mb=required_vram_mb,
            termination_grace_seconds=termination_grace_seconds,
            checkpoint_watch_path=checkpoint_watch_path,
            checkpoint_stall_seconds=checkpoint_stall_seconds,
            evaluation_contract_ref=evaluation_contract_ref,
            evaluation_contract_sha256=evaluation_contract_sha256,
            protected_artifact_report_ref=protected_artifact_report_ref,
            protected_artifact_report_sha256=protected_artifact_report_sha256,
            created_at=now,
            updated_at=now,
        )
        attempt, attempt_created = self._attempt_store.create_or_get(run_dir, candidate)
        if attempt_created:
            append_event(
                run_dir,
                "experiment.attempt.created",
                {
                    "attempt_id": attempt.attempt_id,
                    "session_id": session_id,
                    "job_type": job_type,
                    "attempt_purpose": attempt.attempt_purpose,
                },
            )
        pipeline_job, job_created = create_or_get_pipeline_job(
            run_dir,
            source_id=session_id,
            job_type=job_type,
            evidence_role=job_type,
            idempotency_key=f"experiment_job:{attempt.attempt_id}",
            payload={"session_id": session_id, "attempt_id": attempt.attempt_id},
        )
        attempt = self._attempt_store.bind_pipeline_job(
            run_dir,
            attempt_id=attempt.attempt_id,
            pipeline_job_id=str(pipeline_job["job_id"]),
        )
        if job_created:
            append_event(
                run_dir,
                "experiment.attempt.queued",
                {"attempt_id": attempt.attempt_id, "job_id": pipeline_job["job_id"], "job_type": job_type},
            )
        disposition = "created" if attempt_created else "repaired" if job_created else "reused"
        return ExperimentAttemptStartResult(
            attempt=attempt,
            pipeline_job=pipeline_job,
            disposition=disposition,
        )

    def create_retry(self, run_dir: Path, *, attempt_id: str) -> ExperimentAttemptStartResult:
        retry = self._attempt_store.create_retry_candidate(
            run_dir,
            attempt_id=attempt_id,
            created_at=_utc_now(),
        )
        pipeline_job, created = create_or_get_pipeline_job(
            run_dir,
            source_id=retry.session_id,
            job_type=retry.job_type,
            evidence_role=retry.job_type,
            idempotency_key=f"experiment_job:{retry.attempt_id}",
            payload={
                "session_id": retry.session_id,
                "attempt_id": retry.attempt_id,
                "not_before": retry.retry_not_before,
            },
        )
        retry = self._attempt_store.bind_pipeline_job(
            run_dir,
            attempt_id=retry.attempt_id,
            pipeline_job_id=str(pipeline_job["job_id"]),
        )
        if created:
            append_event(
                run_dir,
                "experiment.attempt.retry_queued",
                {
                    "attempt_id": retry.attempt_id,
                    "retry_of": retry.retry_of,
                    "retry_count": retry.retry_count,
                    "job_id": pipeline_job["job_id"],
                    "not_before": retry.retry_not_before,
                },
            )
        return ExperimentAttemptStartResult(
            attempt=retry,
            pipeline_job=pipeline_job,
            disposition="created" if created else "reused",
        )

    def _require_executable_session(self, run_dir: Path, session_id: str, job_type: AttemptJobType):
        session = self._session_store.load(run_dir, session_id)
        if session is None:
            raise FileNotFoundError("experiment session not found")
        if session.authorization.execution_mode == "plan_only":
            raise ValueError("plan_only Session may not create experiment Attempts")
        if job_type == "experiment_baseline" and session.status != "READY_FOR_BASELINE":
            raise ValueError("baseline Attempt requires Session READY_FOR_BASELINE")
        if job_type != "experiment_baseline" and session.status not in {"READY", "BASELINE_RUNNING"}:
            raise ValueError("experiment Attempt requires a Session ready after baseline")
        return session


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
