"""Strict, durable pipeline-job store guarded by the per-run mutation lock."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from autoad_researcher.core.control_plane.errors import CorruptAuthoritativeStore, IdempotencyConflict
from autoad_researcher.core.control_plane.hashing import pipeline_job_request_sha256
from autoad_researcher.core.control_plane.io import atomic_write_jsonl
from autoad_researcher.core.control_plane.lock import RunMutationLock
from autoad_researcher.core.control_plane.models import PipelineJob


class PipelineJobStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "jobs" / "pipeline_jobs.jsonl"

    def list(self) -> list[PipelineJob]:
        with RunMutationLock(self.run_dir, mode="shared"):
            return self._load_unlocked()

    def enqueue(
        self,
        *,
        source_id: str,
        job_type: str,
        evidence_role: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> PipelineJob:
        body = payload or {}
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            depends_on = body.get("depends_on")
            if depends_on is not None:
                if not isinstance(depends_on, str) or not depends_on:
                    raise ValueError("payload.depends_on must be a non-empty job id")
                if not any(item.job_id == depends_on for item in jobs):
                    raise ValueError(f"payload.depends_on references unknown job: {depends_on}")

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
                        return existing
                    raise IdempotencyConflict(
                        f"job key {idempotency_key!r} reused with different request content"
                    )

            max_id = max((int(item.job_id.removeprefix("job_")) for item in jobs), default=0)
            job = PipelineJob(
                job_id=f"job_{max_id + 1:06d}",
                source_id=source_id,
                job_type=job_type,
                status="queued",
                evidence_role=evidence_role,
                created_at=datetime.now(timezone.utc),
                payload=body,
                idempotency_key=idempotency_key,
                request_sha256=request_hash,
            )
            jobs.append(job)
            self._write_unlocked(jobs)
            return job

    def claim_legacy(self, job_id: str) -> PipelineJob | None:
        """Compatibility claim used until the worker is migrated in Step 2."""
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            for index, job in enumerate(jobs):
                if job.job_id != job_id or job.status != "queued":
                    continue
                now = datetime.now(timezone.utc)
                jobs[index] = job.model_copy(update={
                    "status": "running",
                    "started_at": now,
                    "attempt_started_at": now,
                    "attempt_count": job.attempt_count + 1,
                    "claimed_by": "legacy_worker",
                    "claim_token": f"claim_{uuid4().hex}",
                })
                self._write_unlocked(jobs)
                return jobs[index]
            return None

    def complete_legacy(self, job_id: str, *, outputs: list[str] | None = None) -> PipelineJob | None:
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            for index, job in enumerate(jobs):
                if job.job_id != job_id:
                    continue
                jobs[index] = job.model_copy(update={
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc),
                    "outputs": list(outputs) if outputs is not None else job.outputs,
                })
                self._write_unlocked(jobs)
                return jobs[index]
            return None

    def fail_legacy(self, job_id: str, *, error: str) -> PipelineJob | None:
        with RunMutationLock(self.run_dir, mode="exclusive"):
            jobs = self._load_unlocked()
            for index, job in enumerate(jobs):
                if job.job_id != job_id:
                    continue
                jobs[index] = job.model_copy(update={
                    "status": "failed",
                    "completed_at": datetime.now(timezone.utc),
                    "error": error,
                })
                self._write_unlocked(jobs)
                return jobs[index]
            return None

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
