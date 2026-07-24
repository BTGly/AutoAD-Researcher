from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
from autoad_researcher.assistant.v2.job_service import append_pipeline_job, retry_failed_source_jobs
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400
from autoad_researcher.ui.sources import (
    load_source_registry,
    remove_source,
    save_uploaded_file,
    update_source_intake_result,
)

router = APIRouter(prefix="/api/runs", tags=["sources"])


@router.get("/{run_id}/sources")
async def get_sources(run_id: str):
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    path = run_dir / "sources" / "source_references.json"
    if not path.is_file():
        return []
    import json
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
        return reg.get("sources", [])
    except Exception:
        return []


@router.post("/{run_id}/sources/upload")
async def upload_source(
    run_id: str,
    request: Request,
    x_autoad_filename: str = Header(default=""),
):
    name = Path(unquote(x_autoad_filename)).name
    if not name:
        raise HTTPException(400, "X-AutoAD-Filename header is required")
    content = await request.body()
    if not content:
        raise HTTPException(400, "uploaded file is empty")
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    uploaded = SimpleNamespace(name=name, getvalue=lambda: content)
    try:
        source = save_uploaded_file(run_dir, uploaded)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    jobs: list[dict] = []
    artifacts: list[str] = []
    kind = str(source.get("kind", ""))
    source_id = str(source.get("source_id", ""))
    stored_path = str(source.get("stored_path", ""))

    if kind == "paper_pdf":
        job = append_pipeline_job(
            run_dir,
            source_id=source_id,
            job_type="paper_parse_mineru",
            evidence_role="parsed_paper_evidence",
            payload={"stored_path": stored_path},
        )
        jobs.append(job)
        append_event(run_dir, "job.queued", {
            "job_id": job.get("job_id", ""),
            "job_type": job.get("job_type", ""),
            "source_id": source_id,
        })
    elif kind in {"markdown", "text"}:
        artifacts.append(stored_path)
        append_artifact_evidence(
            run_dir,
            source_id=source_id,
            artifact_path=stored_path,
            evidence_type="uploaded_text",
            parser_name="direct_upload",
            summary=_uploaded_text_preview(run_dir / stored_path),
            raw={"filename": name, "kind": kind},
        )
        append_event(run_dir, "artifact.created", {"source_id": source_id, "paths": artifacts})
        append_event(run_dir, "evidence.updated", {"source_id": source_id})
    elif kind == "document":
        job = append_pipeline_job(
            run_dir,
            source_id=source_id,
            job_type="document_markitdown",
            evidence_role="parsed_document_evidence",
            payload={"stored_path": stored_path},
        )
        jobs.append(job)
        append_event(run_dir, "job.queued", {
            "job_id": job.get("job_id", ""),
            "job_type": job.get("job_type", ""),
            "source_id": source_id,
        })
    elif kind == "archive_bundle":
        job = append_pipeline_job(
            run_dir,
            source_id=source_id,
            job_type="archive_unpack_classify",
            evidence_role="archive_manifest",
            payload={"stored_path": stored_path},
        )
        jobs.append(job)
        append_event(run_dir, "job.queued", {
            "job_id": job.get("job_id", ""),
            "job_type": job.get("job_type", ""),
            "source_id": source_id,
        })
    elif kind == "local_repo":
        unpack_job = append_pipeline_job(
            run_dir,
            source_id=source_id,
            job_type="local_repo_unpack",
            evidence_role="repo_acquired",
            payload={"stored_path": stored_path},
        )
        jobs.append(unpack_job)
        summarize_job = append_pipeline_job(
            run_dir,
            source_id=source_id,
            job_type="repo_summarize",
            evidence_role="repo_acquired",
            payload={"depends_on": unpack_job.get("job_id")},
        )
        jobs.append(summarize_job)
        for job in jobs:
            append_event(run_dir, "job.queued", {
                "job_id": job.get("job_id", ""),
                "job_type": job.get("job_type", ""),
                "source_id": source_id,
            })

    append_event(run_dir, "source.created", {
        "source_id": source_id,
        "kind": kind,
        "stored_path": stored_path,
    })

    return {"source": source, "jobs": jobs, "artifacts": artifacts}


@router.delete("/{run_id}/sources/{source_id}")
async def delete_source(run_id: str, source_id: str):
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run not found")
    removed = remove_source(run_dir, source_id, reason="user_deleted")
    if removed is None:
        raise HTTPException(status_code=404, detail="source not found")
    append_event(run_dir, "source.deleted", {"source_id": source_id})
    append_event(run_dir, "evidence.updated", {"source_id": source_id})
    return {"source_id": source_id, "deleted": True, "removed_evidence": removed["removed_evidence"]}


@router.post("/{run_id}/sources/{source_id}/retry")
async def retry_source(run_id: str, source_id: str):
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    source = next(
        (item for item in load_source_registry(run_dir).get("sources", []) if item.get("source_id") == source_id),
        None,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    try:
        jobs = retry_failed_source_jobs(run_dir, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    status = "uploaded_not_parsed" if source.get("stored_path") else "user_provided_not_ingested"
    update_source_intake_result(
        run_dir,
        source_id,
        status=status,
        intake_status="pending",
        clear_intake_error=True,
        error_message="",
    )
    payload = {
        "source_id": source_id,
        "job_ids": [job.get("job_id", "") for job in jobs],
        "retry_generation": jobs[0].get("retry_generation") if jobs else None,
    }
    append_event(run_dir, "source.retry_queued", payload)
    for job in jobs:
        append_event(run_dir, "job.queued", {
            "job_id": job.get("job_id", ""),
            "job_type": job.get("job_type", ""),
            "source_id": source_id,
        })
    return {"source_id": source_id, "jobs": jobs, "status": status}


def _uploaded_text_preview(path: Path, limit: int = 1200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"
