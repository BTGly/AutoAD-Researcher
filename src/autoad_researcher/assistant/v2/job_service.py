"""Compatibility facade for the canonical control-plane PipelineJobStore."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.core.control_plane import PipelineJobStore

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
}


def load_pipeline_jobs(run_dir: Path) -> list[dict[str, Any]]:
    return [job.model_dump(mode="json", exclude_none=False) for job in PipelineJobStore(run_dir).list()]


def append_pipeline_job(
    run_dir: Path,
    *,
    source_id: str,
    job_type: str,
    evidence_role: str = "",
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    job = PipelineJobStore(run_dir).enqueue(
        source_id=source_id,
        job_type=job_type,
        evidence_role=evidence_role or JOB_TYPES.get(job_type, "candidate_source_only"),
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return job.model_dump(mode="json", exclude_none=False)


def claim_pipeline_job(run_dir: Path, job_id: str) -> dict[str, Any] | None:
    job = PipelineJobStore(run_dir).claim_legacy(job_id)
    return job.model_dump(mode="json", exclude_none=False) if job is not None else None


def complete_pipeline_job(run_dir: Path, job_id: str, *, outputs: list[str] | None = None) -> dict[str, Any] | None:
    job = PipelineJobStore(run_dir).complete_legacy(job_id, outputs=outputs)
    return job.model_dump(mode="json", exclude_none=False) if job is not None else None


def fail_pipeline_job(run_dir: Path, job_id: str, *, error: str) -> dict[str, Any] | None:
    job = PipelineJobStore(run_dir).fail_legacy(job_id, error=error)
    return job.model_dump(mode="json", exclude_none=False) if job is not None else None
