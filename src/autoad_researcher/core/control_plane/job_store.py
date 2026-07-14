"""Strict, durable pipeline-job store guarded by the per-run mutation lock."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from pydantic import ValidationError

from autoad_researcher.core.control_plane.errors import (
    CorruptAuthoritativeStore,
    IdempotencyConflict,
    JobClaimFenceError,
)
from autoad_researcher.core.control_plane.hashing import pipeline_job_request_sha256
from autoad_researcher.core.control_plane.io import atomic_write_jsonl, write_json_exclusive_durable
from autoad_researcher.core.control_plane.lock import RunMutationLock
from autoad_researcher.core.control_plane.models import (
    AttemptResult,
    ClaimRecord,
    JobTransition,
    PipelineJob,
)


EXPERIMENT_PREPARE_JOB_TYPE = "experiment_prepare"
DEFAULT_LEASE_SECONDS = 300
MAX_ATTEMPT_WALL_SECONDS = 15 * 60
MAX_AUTOMATIC_RECOVERIES = 3
RECOVERY_BACKOFF_SECONDS = (5, 15, 45)
_ATTEMPT_DIR_PATTERN = re.compile(r"^attempt_([1-9][0-9]*)_(claim_[0-9a-f]{32})$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _job_sort_key(job: PipelineJob) -> tuple[datetime, int]:
    return job.created_at, int(job.job_id.removeprefix("job_"))


class PipelineJobStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "jobs" / "pipeline_jobs.jsonl"
        self.attempts_root = self.run_dir / "experiment_agents" / "attempts"

    def list(self) -> list[PipelineJob]:
        with RunMutationLock(self.run_dir, mode="shared"):
            return self._load_unlocked()

    def get(self, job_id: str) -> PipelineJob | None:
        with RunMutationLock(self.run_dir, mode="shared"):
            return next((job for job in self._load_unlocked() if job.job_id == job_id), None)

    def enqueue(
        self,
        *,
        source_id: str,
        job_type: str,
        evidence_role: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> PipelineJob:
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            job, created = self._enqueue_unlocked(
                jobs,
                source_id=source_id,
                job_type=job_type,
                evidence_role=evidence_role,
                payload=payload,
                idempotency_key=idempotency_key,
            )
            if not created:
                return job
            self._write_unlocked(jobs)
            return job

    def _enqueue_unlocked(
        self,
        jobs: list[PipelineJob],
        *,
        source_id: str,
        job_type: str,
        evidence_role: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[PipelineJob, bool]:
        body = payload or {}
        request_hash = pipeline_job_request_sha256(
            source_id=source_id,
            job_type=job_type,
            evidence_role=evidence_role,
            payload=body,
        )
        if idempotency_key is not None:
            for existing in jobs:
                if existing.job_type != job_type or existing.idempotency_key != idempotency_key:
                    continue
                if existing.request_sha256 == request_hash:
                    return existing, False
                raise IdempotencyConflict(
                    f"job key {idempotency_key!r} reused with different request content"
                )

        max_id = max((int(item.job_id.removeprefix("job_")) for item in jobs), default=0)
        job_id = f"job_{max_id + 1:06d}"
        depends_on = body.get("depends_on")
        if depends_on is not None:
            if not isinstance(depends_on, str) or not depends_on:
                raise ValueError("payload.depends_on must be a non-empty job id")
            if depends_on == job_id:
                raise ValueError("pipeline job cannot depend on itself")
            if not any(item.job_id == depends_on for item in jobs):
                raise ValueError(f"payload.depends_on references unknown job: {depends_on}")

        job = PipelineJob(
            job_id=job_id,
            source_id=source_id,
            job_type=job_type,
            status="queued",
            evidence_role=evidence_role,
            created_at=_utcnow(),
            payload=body,
            idempotency_key=idempotency_key,
            request_sha256=request_hash,
        )
        jobs.append(job)
        return job, True

    def reconcile_orphan_claims(self) -> list[AttemptResult]:
        """Close claim artifacts that were never activated in the Job Store."""
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = {job.job_id: job for job in self._load_unlocked()}
            results: list[AttemptResult] = []
            for claim, attempt_dir in self._load_claim_records_unlocked():
                if (attempt_dir / "attempt_result.json").is_file():
                    self._load_attempt_result_unlocked(attempt_dir)
                    continue
                job = jobs.get(claim.job_id)
                active = bool(
                    job
                    and job.status == "running"
                    and job.attempt_count == claim.attempt_count
                    and job.claim_token == claim.claim_token
                )
                if active:
                    continue
                results.append(self._ensure_attempt_result_unlocked(
                    attempt_dir,
                    claim,
                    status="claim_aborted",
                    finished_at=_utcnow(),
                    error="claim artifact was not activated in pipeline job state",
                ))
            return results

    def reconcile_job_dependencies(self) -> list[JobTransition]:
        """Fail queued jobs whose single dependency is invalid or terminally failed."""
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            by_id = {job.job_id: job for job in jobs}
            cycle_ids = self._dependency_cycle_ids(jobs)
            transitions: list[JobTransition] = []
            changed = True
            while changed:
                changed = False
                for index, job in enumerate(jobs):
                    if job.status != "queued":
                        continue
                    dependency_id = self._dependency_id(job)
                    reason: str | None = None
                    if job.job_id in cycle_ids:
                        reason = "dependency_cycle"
                    elif dependency_id and dependency_id not in by_id:
                        reason = "dependency_missing"
                    elif dependency_id and by_id[dependency_id].status == "failed":
                        reason = "dependency_failed"
                    if reason is None:
                        continue
                    failed = job.model_copy(update={
                        "status": "failed",
                        "completed_at": _utcnow(),
                        "error": (
                            f"dependency failed: {dependency_id}"
                            if reason == "dependency_failed"
                            else reason
                        ),
                        "next_eligible_at": None,
                    })
                    jobs[index] = failed
                    by_id[job.job_id] = failed
                    transitions.append(JobTransition(
                        job_id=job.job_id,
                        from_status="queued",
                        to_status="failed",
                        reason=reason,
                        attempt_count=job.attempt_count,
                    ))
                    changed = True
            if transitions:
                self._write_unlocked(jobs)
            return transitions

    def claim_next(
        self,
        *,
        worker_id: str,
        allowed_job_types: set[str] | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> PipelineJob | None:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        current = now or _utcnow()
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            by_id = {job.job_id: job for job in jobs}
            eligible = [
                job
                for job in jobs
                if job.status == "queued"
                and (allowed_job_types is None or job.job_type in allowed_job_types)
                and (job.next_eligible_at is None or job.next_eligible_at <= current)
                and self._dependency_completed(job, by_id)
            ]
            if not eligible:
                return None
            selected = min(eligible, key=_job_sort_key)
            attempt_count = selected.attempt_count + 1
            claim_token = f"claim_{uuid4().hex}"
            lease_expires_at = (
                current + timedelta(seconds=lease_seconds)
                if selected.job_type == EXPERIMENT_PREPARE_JOB_TYPE
                else None
            )
            claim = ClaimRecord(
                job_id=selected.job_id,
                attempt_count=attempt_count,
                claim_token=claim_token,
                worker_id=worker_id,
                claimed_at=current,
                lease_expires_at=lease_expires_at,
                control_request_id=selected.pending_control_request_id,
            )
            attempt_dir = self._attempt_dir(selected.job_id, attempt_count, claim_token)
            self._create_claim_artifact_unlocked(attempt_dir, claim)
            claimed = selected.model_copy(update={
                "status": "running",
                "started_at": current,
                "completed_at": None,
                "error": None,
                "outputs": [],
                "attempt_started_at": current,
                "attempt_count": attempt_count,
                "claimed_by": worker_id,
                "claim_token": claim_token,
                "lease_expires_at": lease_expires_at,
                "next_eligible_at": None,
                "active_control_request_id": selected.pending_control_request_id,
                "pending_control_request_id": None,
            })
            jobs[jobs.index(selected)] = claimed
            self._write_unlocked(jobs)
            if selected.job_type == EXPERIMENT_PREPARE_JOB_TYPE:
                from autoad_researcher.core.control_plane.experiment_state import (
                    transition_session_if_present_unlocked,
                )

                transition_session_if_present_unlocked(
                    self.run_dir,
                    prepare_job_id=selected.job_id,
                    status="preparing",
                    now=current,
                )
            return claimed

    def renew_lease(
        self,
        job_id: str,
        *,
        claim_token: str,
        expected_attempt_count: int,
        extend_seconds: int = DEFAULT_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> PipelineJob:
        if extend_seconds <= 0:
            raise ValueError("extend_seconds must be positive")
        current = now or _utcnow()
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            index, job = self._find_job(jobs, job_id)
            self._validate_fence(job, claim_token, expected_attempt_count, current)
            if job.job_type != EXPERIMENT_PREPARE_JOB_TYPE or job.attempt_started_at is None:
                raise JobClaimFenceError(f"job {job_id} does not support lease renewal")
            wall_deadline = job.attempt_started_at + timedelta(seconds=MAX_ATTEMPT_WALL_SECONDS)
            if current >= wall_deadline:
                raise JobClaimFenceError(f"job {job_id} exceeded maximum attempt wall time")
            renewed = job.model_copy(update={
                "lease_expires_at": min(current + timedelta(seconds=extend_seconds), wall_deadline),
            })
            jobs[index] = renewed
            self._write_unlocked(jobs)
            return renewed

    def complete(
        self,
        job_id: str,
        *,
        claim_token: str,
        expected_attempt_count: int,
        outputs: list[str] | None = None,
        now: datetime | None = None,
    ) -> PipelineJob:
        current = now or _utcnow()
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            index, job = self._find_job(jobs, job_id)
            self._validate_fence(job, claim_token, expected_attempt_count, current)
            claim, attempt_dir = self._load_active_claim_unlocked(job)
            result_path = attempt_dir / "attempt_result.json"
            if job.job_type == EXPERIMENT_PREPARE_JOB_TYPE:
                if not result_path.is_file():
                    raise CorruptAuthoritativeStore(
                        f"experiment_prepare job {job_id} cannot complete without AttemptResult"
                    )
                self._load_attempt_result_unlocked(attempt_dir)
            else:
                self._ensure_attempt_result_unlocked(
                    attempt_dir,
                    claim,
                    status="completed",
                    finished_at=current,
                )
            completed = job.model_copy(update={
                "status": "completed",
                "completed_at": current,
                "outputs": list(outputs) if outputs is not None else job.outputs,
                "error": None,
                "claimed_by": None,
                "claim_token": None,
                "attempt_started_at": None,
                "lease_expires_at": None,
                "next_eligible_at": None,
                "active_control_request_id": None,
                "consecutive_stale_count": 0,
                "consecutive_lease_expiry_count": 0,
            })
            jobs[index] = completed
            self._write_unlocked(jobs)
            return completed

    def fail(
        self,
        job_id: str,
        *,
        claim_token: str,
        expected_attempt_count: int,
        error: str,
        now: datetime | None = None,
    ) -> PipelineJob:
        current = now or _utcnow()
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            index, job = self._find_job(jobs, job_id)
            self._validate_fence(job, claim_token, expected_attempt_count, current)
            claim, attempt_dir = self._load_active_claim_unlocked(job)
            if job.job_type == EXPERIMENT_PREPARE_JOB_TYPE:
                from autoad_researcher.core.control_plane.experiment_state import (
                    transition_session_if_present_unlocked,
                )

                transition_session_if_present_unlocked(
                    self.run_dir,
                    prepare_job_id=job.job_id,
                    status="failed",
                    now=current,
                    error=error,
                )
            self._ensure_attempt_result_unlocked(
                attempt_dir,
                claim,
                status="failed",
                finished_at=current,
                error=error,
            )
            failed = job.model_copy(update={
                "status": "failed",
                "completed_at": current,
                "error": error,
                "outputs": [],
                "claimed_by": None,
                "claim_token": None,
                "attempt_started_at": None,
                "lease_expires_at": None,
                "next_eligible_at": None,
                "active_control_request_id": None,
            })
            jobs[index] = failed
            self._write_unlocked(jobs)
            if job.job_type == EXPERIMENT_PREPARE_JOB_TYPE and job.active_control_request_id is not None:
                from autoad_researcher.core.control_plane.materialization_requests import (
                    MaterializationRequestStore,
                )

                MaterializationRequestStore(self.run_dir).mark_terminal_unlocked(
                    job.active_control_request_id,
                    status="failed",
                    now=current,
                    error=error,
                )
            return failed

    def requeue_expired(self, *, now: datetime | None = None) -> list[JobTransition]:
        current = now or _utcnow()
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            transitions: list[JobTransition] = []
            terminal_request_ids: list[str] = []
            for index, job in enumerate(jobs):
                if job.status != "running" or job.job_type != EXPERIMENT_PREPARE_JOB_TYPE:
                    continue
                wall_expired = bool(
                    job.attempt_started_at
                    and current >= job.attempt_started_at + timedelta(seconds=MAX_ATTEMPT_WALL_SECONDS)
                )
                lease_expired = bool(job.lease_expires_at and current >= job.lease_expires_at)
                if not wall_expired and not lease_expired:
                    continue
                claim, attempt_dir = self._load_active_claim_unlocked(job)
                from autoad_researcher.core.control_plane.experiment_state import (
                    transition_session_if_present_unlocked,
                )

                count = job.consecutive_lease_expiry_count + 1
                retry = count <= MAX_AUTOMATIC_RECOVERIES
                transition_session_if_present_unlocked(
                    self.run_dir,
                    prepare_job_id=job.job_id,
                    status="queued" if retry else "failed",
                    now=current,
                    error=None if retry else "repeated_lease_expiry",
                )
                self._ensure_attempt_result_unlocked(
                    attempt_dir,
                    claim,
                    status="lease_lost",
                    finished_at=current,
                    error="maximum attempt wall time exceeded" if wall_expired else "job lease expired",
                )
                next_eligible = (
                    current + timedelta(seconds=RECOVERY_BACKOFF_SECONDS[count - 1])
                    if retry
                    else None
                )
                updated = self._reset_for_requeue(
                    job,
                    pending_control_request_id=job.active_control_request_id if retry else None,
                    next_eligible_at=next_eligible,
                ).model_copy(update={
                    "status": "queued" if retry else "failed",
                    "completed_at": None if retry else current,
                    "error": None if retry else "repeated_lease_expiry",
                    "consecutive_lease_expiry_count": count,
                })
                jobs[index] = updated
                if not retry and job.active_control_request_id is not None:
                    terminal_request_ids.append(job.active_control_request_id)
                transitions.append(JobTransition(
                    job_id=job.job_id,
                    from_status="running",
                    to_status=updated.status,
                    reason="lease_expired" if retry else "repeated_lease_expiry",
                    attempt_count=job.attempt_count,
                ))
            if transitions:
                self._write_unlocked(jobs)
                from autoad_researcher.core.control_plane.materialization_requests import (
                    MaterializationRequestStore,
                )

                request_store = MaterializationRequestStore(self.run_dir)
                for request_id in terminal_request_ids:
                    request_store.mark_terminal_unlocked(
                        request_id,
                        status="failed",
                        now=current,
                        error="repeated_lease_expiry",
                    )
            return transitions

    def requeue_stale_input(
        self,
        job_id: str,
        *,
        claim_token: str,
        expected_attempt_count: int,
        input_sha256: str,
        publication_check_input_sha256: str,
        candidate_sha256: str | None = None,
        now: datetime | None = None,
    ) -> JobTransition:
        """Fence a stale experiment attempt and apply bounded automatic recovery."""
        current = now or _utcnow()
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            index, job = self._find_job(jobs, job_id)
            self._validate_fence(job, claim_token, expected_attempt_count, current)
            if job.job_type != EXPERIMENT_PREPARE_JOB_TYPE:
                raise ValueError("stale-input recovery is only valid for experiment_prepare")
            claim, attempt_dir = self._load_active_claim_unlocked(job)
            from autoad_researcher.core.control_plane.experiment_state import (
                transition_session_if_present_unlocked,
            )

            count = job.consecutive_stale_count + 1
            retry = count <= MAX_AUTOMATIC_RECOVERIES
            transition_session_if_present_unlocked(
                self.run_dir,
                prepare_job_id=job.job_id,
                status="queued" if retry else "failed",
                now=current,
                error=None if retry else "input_unstable",
            )
            self._ensure_attempt_result_unlocked(
                attempt_dir,
                claim,
                status="stale_input",
                finished_at=current,
                error="materialization input changed before publication",
                input_sha256=input_sha256,
                publication_check_input_sha256=publication_check_input_sha256,
                candidate_sha256=candidate_sha256,
            )
            next_eligible = (
                current + timedelta(seconds=RECOVERY_BACKOFF_SECONDS[count - 1])
                if retry
                else None
            )
            updated = self._reset_for_requeue(
                job,
                pending_control_request_id=job.active_control_request_id if retry else None,
                next_eligible_at=next_eligible,
            ).model_copy(update={
                "status": "queued" if retry else "failed",
                "completed_at": None if retry else current,
                "error": None if retry else "input_unstable",
                "consecutive_stale_count": count,
            })
            jobs[index] = updated
            self._write_unlocked(jobs)
            if not retry and job.active_control_request_id is not None:
                from autoad_researcher.core.control_plane.materialization_requests import (
                    MaterializationRequestStore,
                )

                MaterializationRequestStore(self.run_dir).mark_terminal_unlocked(
                    job.active_control_request_id,
                    status="failed",
                    now=current,
                    error="input_unstable",
                )
            return JobTransition(
                job_id=job.job_id,
                from_status="running",
                to_status=updated.status,
                reason="stale_input" if retry else "input_unstable",
                attempt_count=job.attempt_count,
            )

    def retry_failed(self, job_id: str) -> PipelineJob:
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            index, job = self._find_job(jobs, job_id)
            if job.status != "failed":
                raise ValueError(f"job {job_id} is not failed")
            queued = self._reset_for_requeue(
                job,
                pending_control_request_id=job.pending_control_request_id,
                next_eligible_at=None,
            ).model_copy(update={
                "consecutive_stale_count": 0,
                "consecutive_lease_expiry_count": 0,
            })
            jobs[index] = queued
            self._write_unlocked(jobs)
            if job.job_type == EXPERIMENT_PREPARE_JOB_TYPE:
                from autoad_researcher.core.control_plane.experiment_state import (
                    transition_session_if_present_unlocked,
                )

                transition_session_if_present_unlocked(
                    self.run_dir,
                    prepare_job_id=job.job_id,
                    status="queued",
                    now=_utcnow(),
                )
            return queued

    # Compatibility methods remain for call sites outside the migrated worker.
    def claim_legacy(self, job_id: str) -> PipelineJob | None:
        return self._claim_specific_legacy(job_id)

    def complete_legacy(self, job_id: str, *, outputs: list[str] | None = None) -> PipelineJob | None:
        job = self.get(job_id)
        if job is None:
            return None
        if job.status != "running" or not job.claim_token:
            return job
        return self.complete(
            job_id,
            claim_token=job.claim_token,
            expected_attempt_count=job.attempt_count,
            outputs=outputs,
        )

    def fail_legacy(self, job_id: str, *, error: str) -> PipelineJob | None:
        job = self.get(job_id)
        if job is None:
            return None
        if job.status == "queued":
            claimed = self._claim_specific_legacy(job_id)
            if claimed is None:
                return None
            job = claimed
        if job.status != "running" or not job.claim_token:
            return job
        return self.fail(
            job_id,
            claim_token=job.claim_token,
            expected_attempt_count=job.attempt_count,
            error=error,
        )

    def _claim_specific_legacy(self, job_id: str) -> PipelineJob | None:
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            try:
                index, selected = self._find_job(jobs, job_id)
            except KeyError:
                return None
            if selected.status != "queued":
                return None
            current = _utcnow()
            attempt_count = selected.attempt_count + 1
            claim_token = f"claim_{uuid4().hex}"
            claim = ClaimRecord(
                job_id=job_id,
                attempt_count=attempt_count,
                claim_token=claim_token,
                worker_id="legacy_worker",
                claimed_at=current,
            )
            attempt_dir = self._attempt_dir(job_id, attempt_count, claim_token)
            self._create_claim_artifact_unlocked(attempt_dir, claim)
            claimed = selected.model_copy(update={
                "status": "running",
                "started_at": current,
                "attempt_started_at": current,
                "attempt_count": attempt_count,
                "claimed_by": "legacy_worker",
                "claim_token": claim_token,
            })
            jobs[index] = claimed
            self._write_unlocked(jobs)
            return claimed

    @staticmethod
    def _dependency_id(job: PipelineJob) -> str | None:
        value = job.payload.get("depends_on")
        return value if isinstance(value, str) and value else None

    def _dependency_completed(self, job: PipelineJob, by_id: dict[str, PipelineJob]) -> bool:
        dependency_id = self._dependency_id(job)
        return dependency_id is None or (
            dependency_id in by_id and by_id[dependency_id].status == "completed"
        )

    def _dependency_cycle_ids(self, jobs: Iterable[PipelineJob]) -> set[str]:
        graph = {
            job.job_id: dependency
            for job in jobs
            if (dependency := self._dependency_id(job)) is not None
        }
        cycles: set[str] = set()
        done: set[str] = set()
        for start in graph:
            if start in done:
                continue
            trail: list[str] = []
            positions: dict[str, int] = {}
            node: str | None = start
            while node is not None and node in graph and node not in done:
                if node in positions:
                    cycles.update(trail[positions[node]:])
                    break
                positions[node] = len(trail)
                trail.append(node)
                node = graph.get(node)
            done.update(trail)
        return cycles

    def _validate_fence(
        self,
        job: PipelineJob,
        claim_token: str,
        expected_attempt_count: int,
        now: datetime,
    ) -> None:
        if (
            job.status != "running"
            or job.claim_token != claim_token
            or job.attempt_count != expected_attempt_count
        ):
            raise JobClaimFenceError(f"claim fence rejected job {job.job_id}")
        if job.lease_expires_at is not None and now >= job.lease_expires_at:
            raise JobClaimFenceError(f"job {job.job_id} lease has expired")
        if (
            job.attempt_started_at is not None
            and job.job_type == EXPERIMENT_PREPARE_JOB_TYPE
            and now >= job.attempt_started_at + timedelta(seconds=MAX_ATTEMPT_WALL_SECONDS)
        ):
            raise JobClaimFenceError(f"job {job.job_id} exceeded maximum attempt wall time")

    @staticmethod
    def _find_job(jobs: list[PipelineJob], job_id: str) -> tuple[int, PipelineJob]:
        for index, job in enumerate(jobs):
            if job.job_id == job_id:
                return index, job
        raise KeyError(f"pipeline job not found: {job_id}")

    @staticmethod
    def _reset_for_requeue(
        job: PipelineJob,
        *,
        pending_control_request_id: str | None,
        next_eligible_at: datetime | None,
    ) -> PipelineJob:
        return job.model_copy(update={
            "status": "queued",
            "claimed_by": None,
            "claim_token": None,
            "attempt_started_at": None,
            "lease_expires_at": None,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "outputs": [],
            "active_control_request_id": None,
            "pending_control_request_id": pending_control_request_id,
            "next_eligible_at": next_eligible_at,
        })

    def _attempt_dir(self, job_id: str, attempt_count: int, claim_token: str) -> Path:
        return self.attempts_root / job_id / f"attempt_{attempt_count}_{claim_token}"

    def _load_active_claim_unlocked(self, job: PipelineJob) -> tuple[ClaimRecord, Path]:
        if job.claim_token is None:
            raise CorruptAuthoritativeStore(f"running job {job.job_id} has no claim token")
        attempt_dir = self._attempt_dir(job.job_id, job.attempt_count, job.claim_token)
        path = attempt_dir / "claim.json"
        try:
            claim = ClaimRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise CorruptAuthoritativeStore(f"invalid active claim artifact: {path}") from exc
        if (
            claim.job_id != job.job_id
            or claim.attempt_count != job.attempt_count
            or claim.claim_token != job.claim_token
        ):
            raise CorruptAuthoritativeStore(f"active claim identity mismatch: {path}")
        return claim, attempt_dir

    def _load_claim_records_unlocked(self) -> list[tuple[ClaimRecord, Path]]:
        if not self.attempts_root.is_dir():
            return []
        records: list[tuple[ClaimRecord, Path]] = []
        for job_dir in sorted(self.attempts_root.iterdir()):
            if not job_dir.is_dir() or not re.fullmatch(r"job_[0-9]{6}", job_dir.name):
                continue
            for attempt_dir in sorted(job_dir.iterdir()):
                if not attempt_dir.is_dir():
                    continue
                match = _ATTEMPT_DIR_PATTERN.fullmatch(attempt_dir.name)
                if match is None:
                    continue
                path = attempt_dir / "claim.json"
                try:
                    claim = ClaimRecord.model_validate_json(path.read_text(encoding="utf-8"))
                except (OSError, ValidationError, ValueError) as exc:
                    raise CorruptAuthoritativeStore(f"invalid claim artifact: {path}") from exc
                if (
                    claim.job_id != job_dir.name
                    or claim.attempt_count != int(match.group(1))
                    or claim.claim_token != match.group(2)
                ):
                    raise CorruptAuthoritativeStore(f"claim path identity mismatch: {path}")
                records.append((claim, attempt_dir))
        return records

    def _load_attempt_result_unlocked(self, attempt_dir: Path) -> AttemptResult:
        path = attempt_dir / "attempt_result.json"
        try:
            return AttemptResult.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise CorruptAuthoritativeStore(f"invalid attempt result: {path}") from exc

    def _ensure_attempt_result_unlocked(
        self,
        attempt_dir: Path,
        claim: ClaimRecord,
        *,
        status: str,
        finished_at: datetime,
        error: str | None = None,
        input_sha256: str | None = None,
        publication_check_input_sha256: str | None = None,
        candidate_sha256: str | None = None,
        canonical_readiness_sha256: str | None = None,
    ) -> AttemptResult:
        path = attempt_dir / "attempt_result.json"
        if path.is_file():
            existing = self._load_attempt_result_unlocked(attempt_dir)
            if existing.status != status:
                raise CorruptAuthoritativeStore(
                    f"attempt result status conflict at {path}: {existing.status} != {status}"
                )
            return existing
        result = AttemptResult(
            job_id=claim.job_id,
            attempt_count=claim.attempt_count,
            claim_token=claim.claim_token,
            worker_id=claim.worker_id,
            status=status,
            control_request_id=claim.control_request_id,
            started_at=claim.claimed_at,
            finished_at=finished_at,
            error=error,
            input_sha256=input_sha256,
            publication_check_input_sha256=publication_check_input_sha256,
            candidate_sha256=candidate_sha256,
            canonical_readiness_sha256=canonical_readiness_sha256,
        )
        self._write_immutable_model_unlocked(path, result)
        return result

    @staticmethod
    def _write_immutable_model_unlocked(path: Path, model: ClaimRecord | AttemptResult) -> None:
        try:
            write_json_exclusive_durable(path, model.model_dump(mode="json", exclude_none=True))
        except ValueError as exc:
            raise CorruptAuthoritativeStore(str(exc)) from exc

    def _create_claim_artifact_unlocked(self, attempt_dir: Path, claim: ClaimRecord) -> None:
        attempt_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            attempt_dir.mkdir()
        except FileExistsError:
            path = attempt_dir / "claim.json"
            try:
                existing = ClaimRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, ValueError) as exc:
                raise CorruptAuthoritativeStore(f"attempt directory already exists: {attempt_dir}") from exc
            if existing != claim:
                raise CorruptAuthoritativeStore(f"attempt claim conflict: {path}")
            return
        self._write_immutable_model_unlocked(attempt_dir / "claim.json", claim)

    def _load_unlocked(self) -> list[PipelineJob]:
        if not self.path.is_file():
            return []
        jobs: list[PipelineJob] = []
        seen: set[str] = set()
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                job = PipelineJob.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                raise CorruptAuthoritativeStore(f"invalid job at {self.path}:{line_no}") from exc
            if job.job_id in seen:
                raise CorruptAuthoritativeStore(f"duplicate job_id={job.job_id} at {self.path}:{line_no}")
            seen.add(job.job_id)
            jobs.append(job)
        return jobs

    def _write_unlocked(self, jobs: list[PipelineJob]) -> None:
        atomic_write_jsonl(
            self.path,
            [job.model_dump(mode="json", exclude_none=True) for job in jobs],
        )
