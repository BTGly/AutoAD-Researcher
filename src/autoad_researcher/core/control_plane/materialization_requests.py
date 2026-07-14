"""Durable idempotent materialization-request ledger."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from autoad_researcher.core.control_plane.errors import (
    ControlPlaneLockError,
    CorruptAuthoritativeStore,
    IdempotencyConflict,
)
from autoad_researcher.core.control_plane.experiment_state import (
    load_session_unlocked,
    transition_session_if_present_unlocked,
)
from autoad_researcher.core.control_plane.io import atomic_write_jsonl
from autoad_researcher.core.control_plane.models import MaterializationRequestRecord
from autoad_researcher.core.control_plane.unit_of_work import ControlPlaneUnitOfWork


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MaterializationRequestStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "experiment_agents" / "materialization_requests.jsonl"

    def list(self) -> list[MaterializationRequestRecord]:
        from autoad_researcher.core.control_plane.lock import RunMutationLock

        with RunMutationLock(self.run_dir, mode="shared"):
            return self._load_unlocked()

    def get(self, request_id: str) -> MaterializationRequestRecord | None:
        from autoad_researcher.core.control_plane.lock import RunMutationLock

        with RunMutationLock(self.run_dir, mode="shared"):
            return next(
                (record for record in self._load_unlocked() if record.request_id == request_id),
                None,
            )

    def request(
        self,
        *,
        request_id: str,
        force: bool,
        reason: str,
        require_failed: bool = False,
        now: datetime | None = None,
    ) -> MaterializationRequestRecord:
        request_id = request_id.strip()
        reason = reason.strip()
        if not request_id or not reason:
            raise ValueError("materialization request_id and reason must not be empty")
        current = now or _utcnow()
        with ControlPlaneUnitOfWork(self.run_dir) as uow:
            records = self._load_unlocked()
            existing = next((row for row in records if row.request_id == request_id), None)
            if existing is not None:
                if existing.force == force and existing.reason == reason:
                    return existing
                raise IdempotencyConflict(
                    f"materialization request {request_id!r} reused with different parameters"
                )

            session = load_session_unlocked(self.run_dir)
            if session is None:
                raise ValueError("experiment session has not been created")
            jobs = uow.jobs._load_unlocked()
            try:
                index, job = uow.jobs._find_job(jobs, session.prepare_job_id)
            except KeyError as exc:
                raise CorruptAuthoritativeStore("ExperimentSession prepare job is missing") from exc

            scheduled = [
                record
                for record in records
                if record.active_job_id == job.job_id and record.status == "scheduled"
            ]
            if scheduled:
                record = MaterializationRequestRecord(
                    request_id=request_id,
                    force=force,
                    reason=reason,
                    action="not_scheduled",
                    status="not_scheduled",
                    executed=False,
                    active_job_id=job.job_id,
                    created_at=current,
                    error="materialization_request_already_scheduled",
                )
                records.append(record)
                self._write_unlocked(records)
                return record

            if require_failed and job.status != "failed":
                raise ValueError(f"experiment_prepare job is not failed: {job.status}")
            if job.status in {"queued", "running"}:
                record = MaterializationRequestRecord(
                    request_id=request_id,
                    force=force,
                    reason=reason,
                    action="not_scheduled",
                    status="not_scheduled",
                    executed=False,
                    active_job_id=job.job_id,
                    created_at=current,
                    error="job_already_running",
                )
                records.append(record)
                self._write_unlocked(records)
                return record

            record = MaterializationRequestRecord(
                request_id=request_id,
                force=force,
                reason=reason,
                action="scheduled",
                status="scheduled",
                executed=True,
                active_job_id=job.job_id,
                created_at=current,
            )
            records.append(record)
            self._write_unlocked(records)
            queued = uow.jobs._reset_for_requeue(
                job,
                pending_control_request_id=request_id,
                next_eligible_at=None,
            ).model_copy(update={
                "consecutive_stale_count": 0,
                "consecutive_lease_expiry_count": 0,
            })
            transition_session_if_present_unlocked(
                self.run_dir,
                prepare_job_id=job.job_id,
                status="queued",
                now=current,
            )
            jobs[index] = queued
            uow.jobs._write_unlocked(jobs)
            return record

    def reconcile_unlocked(
        self,
        uow: ControlPlaneUnitOfWork,
        *,
        now: datetime,
    ) -> list[MaterializationRequestRecord]:
        """Repair recoverable request/Job tears while the run lock is held."""
        from autoad_researcher.core.control_plane.lock import run_lock_active

        if not run_lock_active(self.run_dir):
            raise ControlPlaneLockError("materialization request reconciliation requires the run lock")
        records = self._load_unlocked()
        jobs = uow.jobs._load_unlocked()
        jobs_by_id = {job.job_id: job for job in jobs}
        records_by_id = {record.request_id: record for record in records}

        scheduled_by_job: dict[str, list[MaterializationRequestRecord]] = {}
        for record in records:
            if record.status == "scheduled":
                scheduled_by_job.setdefault(record.active_job_id, []).append(record)
        for job_id, scheduled in scheduled_by_job.items():
            if len(scheduled) > 1:
                raise CorruptAuthoritativeStore(
                    f"multiple scheduled materialization requests for job {job_id}"
                )

        changed_jobs = False
        changed_records = False
        for job_index, job in enumerate(jobs):
            pointers = [
                request_id
                for request_id in (job.pending_control_request_id, job.active_control_request_id)
                if request_id is not None
            ]
            if len(pointers) > 1:
                raise CorruptAuthoritativeStore(
                    f"job {job.job_id} has both pending and active control requests"
                )
            for request_id in pointers:
                record = records_by_id.get(request_id)
                if record is None:
                    raise CorruptAuthoritativeStore(
                        f"job {job.job_id} references missing materialization request {request_id}"
                    )
                if record.active_job_id != job.job_id:
                    raise CorruptAuthoritativeStore(
                        f"materialization request {request_id} references a different job"
                    )
                if record.status != "scheduled":
                    if job.status in {"queued", "running"}:
                        raise CorruptAuthoritativeStore(
                            f"active job {job.job_id} references terminal request {request_id}"
                        )
                    jobs[job_index] = job.model_copy(update={
                        "pending_control_request_id": None,
                        "active_control_request_id": None,
                    })
                    changed_jobs = True

        for job_id, scheduled in scheduled_by_job.items():
            record = scheduled[0]
            job = jobs_by_id.get(job_id)
            if job is None or job.job_type != "experiment_prepare":
                raise CorruptAuthoritativeStore(
                    f"materialization request {record.request_id} references missing prepare job"
                )
            if job.status == "queued":
                if (
                    job.pending_control_request_id != record.request_id
                    or job.active_control_request_id is not None
                ):
                    raise CorruptAuthoritativeStore(
                        f"queued job {job_id} is not bound to scheduled request {record.request_id}"
                    )
                continue
            if job.status == "running":
                if (
                    job.active_control_request_id != record.request_id
                    or job.pending_control_request_id is not None
                ):
                    raise CorruptAuthoritativeStore(
                        f"running job {job_id} is not bound to scheduled request {record.request_id}"
                    )
                continue

            matching_results = []
            for claim, attempt_dir in uow.jobs._load_claim_records_unlocked():
                if claim.job_id != job_id or claim.control_request_id != record.request_id:
                    continue
                result_path = attempt_dir / "attempt_result.json"
                if not result_path.is_file():
                    continue
                result = uow.jobs._load_attempt_result_unlocked(attempt_dir)
                uow.jobs._validate_attempt_result_identity_unlocked(result, claim, attempt_dir)
                if result.attempt_count == job.attempt_count:
                    matching_results.append(result)

            terminal_status: str | None = None
            terminal_error: str | None = None
            terminal_time = now
            if job.status == "completed" and len(matching_results) == 1:
                result = matching_results[0]
                if result.status not in {"published", "no_op"}:
                    raise CorruptAuthoritativeStore(
                        f"completed job {job_id} has incompatible request attempt {result.status}"
                    )
                terminal_status = "completed"
                terminal_time = result.finished_at
            elif job.status == "failed" and len(matching_results) == 1:
                result = matching_results[0]
                if result.status not in {"failed", "lease_lost", "stale_input"}:
                    raise CorruptAuthoritativeStore(
                        f"failed job {job_id} has incompatible request attempt {result.status}"
                    )
                terminal_status = "failed"
                terminal_error = str(job.error) if job.error is not None else result.error
                terminal_time = result.finished_at
            elif len(matching_results) > 1:
                raise CorruptAuthoritativeStore(
                    f"request {record.request_id} has multiple terminal results for current attempt"
                )

            record_index = records.index(record)
            job_index = next(index for index, item in enumerate(jobs) if item.job_id == job_id)
            if terminal_status is not None:
                updated_record = record.model_copy(update={
                    "status": terminal_status,
                    "completed_at": terminal_time,
                    "error": terminal_error,
                })
                records[record_index] = updated_record
                records_by_id[record.request_id] = updated_record
                jobs[job_index] = jobs[job_index].model_copy(update={
                    "pending_control_request_id": None,
                    "active_control_request_id": None,
                })
                changed_records = True
                changed_jobs = True
                continue

            queued = uow.jobs._reset_for_requeue(
                jobs[job_index],
                pending_control_request_id=record.request_id,
                next_eligible_at=None,
            ).model_copy(update={
                "consecutive_stale_count": 0,
                "consecutive_lease_expiry_count": 0,
            })
            jobs[job_index] = queued
            from autoad_researcher.core.control_plane.experiment_state import (
                transition_session_if_present_unlocked,
            )

            transition_session_if_present_unlocked(
                self.run_dir,
                prepare_job_id=job_id,
                status="queued",
                now=now,
            )
            changed_jobs = True

        if changed_records:
            self._write_unlocked(records)
        if changed_jobs:
            uow.jobs._write_unlocked(jobs)
        return records

    def mark_terminal_unlocked(
        self,
        request_id: str,
        *,
        status: str,
        now: datetime,
        error: str | None = None,
    ) -> MaterializationRequestRecord:
        records = self._load_unlocked()
        for index, record in enumerate(records):
            if record.request_id != request_id:
                continue
            if record.status == status and record.error == error:
                return record
            if record.status != "scheduled":
                raise CorruptAuthoritativeStore(
                    f"materialization request {request_id} cannot transition from {record.status}"
                )
            updated = record.model_copy(update={
                "status": status,
                "completed_at": now,
                "error": error,
            })
            records[index] = updated
            self._write_unlocked(records)
            return updated
        raise CorruptAuthoritativeStore(f"materialization request ledger is missing {request_id}")

    def _load_unlocked(self) -> list[MaterializationRequestRecord]:
        if not self.path.is_file():
            return []
        records: list[MaterializationRequestRecord] = []
        seen: set[str] = set()
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = MaterializationRequestRecord.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                raise CorruptAuthoritativeStore(
                    f"invalid materialization request at {self.path}:{line_no}"
                ) from exc
            if record.request_id in seen:
                raise CorruptAuthoritativeStore(
                    f"duplicate request_id={record.request_id} at {self.path}:{line_no}"
                )
            seen.add(record.request_id)
            records.append(record)
        return records

    def _write_unlocked(self, records: list[MaterializationRequestRecord]) -> None:
        atomic_write_jsonl(
            self.path,
            [record.model_dump(mode="json", exclude_none=True) for record in records],
        )
