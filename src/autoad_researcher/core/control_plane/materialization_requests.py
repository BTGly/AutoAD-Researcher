"""Durable idempotent materialization-request ledger."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from autoad_researcher.core.control_plane.errors import (
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
