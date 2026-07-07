"""PipelineJob service for V2. JSONL-based CRUD with file locking.

Path: runs/{run_id}/jobs/pipeline_jobs.jsonl
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.ui.material_requests import _material_requests_lock as _file_lock

JOBS_DIR = "jobs"
JOBS_FILE = "pipeline_jobs.jsonl"

JOB_TYPES = {
    "web_search": "candidate_source_only",
    "web_fetch": "source_acquired_unparsed",
    "git_clone": "repo_acquired",
    "paper_parse": "parsed_paper_evidence",
    "repo_analyze": "repo_acquired",
}


def _jobs_path(run_dir: Path) -> Path:
    return run_dir / JOBS_DIR / JOBS_FILE


def _generate_job_id(run_dir: Path) -> str:
    existing = load_pipeline_jobs(run_dir)
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
) -> dict[str, Any]:
    job = {
        "job_id": _generate_job_id(run_dir),
        "source_id": source_id,
        "job_type": job_type,
        "status": "queued",
        "evidence_role": evidence_role or JOB_TYPES.get(job_type, "candidate_source_only"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
        "outputs": [],
        "error": None,
    }
    path = _jobs_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(job, ensure_ascii=False) + "\n")
    return job


@contextmanager
def _jobs_lock(run_dir: Path, timeout: float = 5.0):
    lock_path = run_dir / JOBS_DIR / ".pipeline_jobs.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    yield


def claim_pipeline_job(run_dir: Path, job_id: str) -> dict[str, Any] | None:
    jobs = load_pipeline_jobs(run_dir)
    for j in jobs:
        if j["job_id"] == job_id and j.get("status") in ("queued",):
            j["status"] = "running"
            j["started_at"] = datetime.now(timezone.utc).isoformat()
            _write_jobs(run_dir, jobs)
            return j
    return None


def complete_pipeline_job(run_dir: Path, job_id: str, *, outputs: list[str] | None = None) -> dict[str, Any] | None:
    jobs = load_pipeline_jobs(run_dir)
    for j in jobs:
        if j["job_id"] == job_id:
            j["status"] = "completed"
            j["completed_at"] = datetime.now(timezone.utc).isoformat()
            if outputs:
                j["outputs"] = outputs
            _write_jobs(run_dir, jobs)
            return j
    return None


def fail_pipeline_job(run_dir: Path, job_id: str, *, error: str) -> dict[str, Any] | None:
    jobs = load_pipeline_jobs(run_dir)
    for j in jobs:
        if j["job_id"] == job_id:
            j["status"] = "failed"
            j["completed_at"] = datetime.now(timezone.utc).isoformat()
            j["error"] = error
            _write_jobs(run_dir, jobs)
            return j
    return None


def _write_jobs(run_dir: Path, jobs: list[dict[str, Any]]) -> None:
    path = _jobs_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(j, ensure_ascii=False) for j in jobs) + "\n", encoding="utf-8")
