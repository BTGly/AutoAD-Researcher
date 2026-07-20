"""PipelineJob service for V2. JSONL-based CRUD with file locking.

Path: runs/{run_id}/jobs/pipeline_jobs.jsonl
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JOBS_DIR = "jobs"
JOBS_FILE = "pipeline_jobs.jsonl"

JOB_TYPES = {
    "web_search": "candidate_source_only",
    "web_fetch": "source_acquired_unparsed",
    "web_markitdown": "parsed_web_evidence",
    "git_clone": "repo_acquired",
    "local_repo_unpack": "repo_acquired",
    "local_repo_acquire": "repo_acquired",
    "archive_unpack_classify": "archive_manifest",
    "document_markitdown": "parsed_document_evidence",
    "paper_parse": "parsed_paper_evidence",
    "paper_download": "source_acquired_unparsed",
    "paper_parse_mineru": "parsed_paper_evidence",
    "paper_parse_markitdown": "parsed_paper_evidence",
    "paper_summarize": "parsed_paper_evidence",
    "repo_analyze": "repo_acquired",
    "repo_summarize": "repo_acquired",
    "report_snapshot_build": "report_artifact",
    "report_facts_assemble": "report_artifact",
}


def _jobs_path(run_dir: Path) -> Path:
    return run_dir / JOBS_DIR / JOBS_FILE


def _generate_job_id(run_dir: Path) -> str:
    return _next_id_from_loaded(load_pipeline_jobs(run_dir))


def _next_id_from_loaded(existing: list[dict[str, Any]]) -> str:
    max_n = 0
    for j in existing:
        jid = j.get("job_id", "")
        if jid.startswith("job_"):
            try:
                max_n = max(max_n, int(jid[4:]))
            except ValueError:
                pass
    return f"job_{max_n + 1:06d}"


def load_pipeline_jobs(run_dir: Path) -> list[dict[str, Any]]:
    return _load_jobs_unlocked(run_dir)


def _load_jobs_unlocked(run_dir: Path) -> list[dict[str, Any]]:
    path = _jobs_path(run_dir)
    if not path.is_file():
        return []
    jobs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                jobs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return jobs


def append_pipeline_job(
    run_dir: Path,
    *,
    source_id: str,
    job_type: str,
    evidence_role: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _jobs_lock(run_dir):
        jobs = _load_jobs_unlocked(run_dir)
        job = _new_pipeline_job(
            jobs,
            source_id=source_id,
            job_type=job_type,
            evidence_role=evidence_role,
            payload=payload,
            idempotency_key=None,
        )
        jobs.append(job)
        _write_jobs_unlocked(run_dir, jobs)
    return job


def create_or_get_pipeline_job(
    run_dir: Path,
    *,
    source_id: str,
    job_type: str,
    idempotency_key: str,
    evidence_role: str = "",
    payload: dict[str, Any] | None = None,
    report_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create one durable job for an idempotency key, or return the existing one.

    All identity checks and ID allocation occur while holding the jobs lock so a
    concurrent replay cannot allocate a second Job for the same command.
    """
    if not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    normalized_payload = payload or {}
    resolved_role = evidence_role or JOB_TYPES.get(job_type, "candidate_source_only")
    with _jobs_lock(run_dir):
        jobs = _load_jobs_unlocked(run_dir)
        existing = next(
            (job for job in jobs if job.get("idempotency_key") == idempotency_key),
            None,
        )
        if existing is not None:
            identity = {
                "source_id": source_id,
                "job_type": job_type,
                "evidence_role": resolved_role,
                "payload": normalized_payload,
                "report_id": report_id,
            }
            existing_identity = {
                "source_id": existing.get("source_id", ""),
                "job_type": existing.get("job_type"),
                "evidence_role": existing.get("evidence_role", ""),
                "payload": existing.get("payload", {}),
                "report_id": existing.get("report_id"),
            }
            if existing_identity != identity:
                raise ValueError("same idempotency key, different job identity")
            return existing, False

        job = _new_pipeline_job(
            jobs,
            source_id=source_id,
            job_type=job_type,
            evidence_role=resolved_role,
            payload=normalized_payload,
            idempotency_key=idempotency_key,
            report_id=report_id,
        )
        jobs.append(job)
        _write_jobs_unlocked(run_dir, jobs)
        return job, True


def _new_pipeline_job(
    jobs: list[dict[str, Any]],
    *,
    source_id: str,
    job_type: str,
    evidence_role: str,
    payload: dict[str, Any] | None,
    idempotency_key: str | None,
    report_id: str | None = None,
) -> dict[str, Any]:
    return {
        "job_id": _next_id_from_loaded(jobs),
        "source_id": source_id,
        "job_type": job_type,
        "status": "queued",
        "evidence_role": evidence_role or JOB_TYPES.get(job_type, "candidate_source_only"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
        "outputs": [],
        "error": None,
        "payload": payload or {},
        "idempotency_key": idempotency_key,
        "report_id": report_id,
    }


@contextmanager
def _jobs_lock(run_dir: Path, timeout: float = 5.0):
    lock_path = run_dir / JOBS_DIR / ".pipeline_jobs.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    fd = None
    while time.time() < deadline:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            time.sleep(0.1)
    if fd is None:
        raise TimeoutError(f"Could not acquire jobs lock for {run_dir} within {timeout}s")
    try:
        yield
    finally:
        os.close(fd)
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def claim_pipeline_job(run_dir: Path, job_id: str) -> dict[str, Any] | None:
    with _jobs_lock(run_dir):
        jobs = _load_jobs_unlocked(run_dir)
        for j in jobs:
            if j["job_id"] == job_id and j.get("status") in ("queued",):
                j["status"] = "running"
                j["started_at"] = datetime.now(timezone.utc).isoformat()
                _write_jobs_unlocked(run_dir, jobs)
                return j
    return None


def complete_pipeline_job(run_dir: Path, job_id: str, *, outputs: list[str] | None = None) -> dict[str, Any] | None:
    with _jobs_lock(run_dir):
        jobs = _load_jobs_unlocked(run_dir)
        for j in jobs:
            if j["job_id"] == job_id:
                j["status"] = "completed"
                j["completed_at"] = datetime.now(timezone.utc).isoformat()
                if outputs:
                    j["outputs"] = outputs
                _write_jobs_unlocked(run_dir, jobs)
                return j
    return None


def fail_pipeline_job(run_dir: Path, job_id: str, *, error: str) -> dict[str, Any] | None:
    with _jobs_lock(run_dir):
        jobs = _load_jobs_unlocked(run_dir)
        for j in jobs:
            if j["job_id"] == job_id:
                j["status"] = "failed"
                j["completed_at"] = datetime.now(timezone.utc).isoformat()
                j["error"] = error
                _write_jobs_unlocked(run_dir, jobs)
                return j
    return None


def requeue_stale_running_jobs(
    run_dir: Path,
    *,
    stale_after_seconds: int = 300,
    now: datetime | None = None,
    excluded_job_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return abandoned running Jobs to queued state after their recovery lease.

    A fresh Worker never claims an active Job immediately.  It only repairs a
    Job whose persisted ``started_at`` has exceeded this bounded lease.
    """
    if stale_after_seconds < 0:
        raise ValueError("stale_after_seconds must be non-negative")
    current = now or datetime.now(timezone.utc)
    stale_before = current - timedelta(seconds=stale_after_seconds)
    recovered: list[dict[str, Any]] = []
    with _jobs_lock(run_dir):
        jobs = _load_jobs_unlocked(run_dir)
        changed = False
        for job in jobs:
            if job.get("status") != "running":
                continue
            if job.get("job_type") in (excluded_job_types or set()):
                continue
            started_at = _parse_datetime(job.get("started_at"))
            if started_at is not None and started_at > stale_before:
                continue
            job["status"] = "queued"
            job["started_at"] = None
            job["completed_at"] = None
            job["error"] = None
            job["recovery_count"] = int(job.get("recovery_count", 0)) + 1
            recovered.append(dict(job))
            changed = True
        if changed:
            _write_jobs_unlocked(run_dir, jobs)
    return recovered


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _write_jobs_unlocked(run_dir: Path, jobs: list[dict[str, Any]]) -> None:
    path = _jobs_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(job, ensure_ascii=False) for job in jobs) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
