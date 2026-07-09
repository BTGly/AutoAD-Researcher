from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
from autoad_researcher.assistant.v2.job_service import append_pipeline_job
from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.ui.sources import remove_source, save_uploaded_file

router = APIRouter(prefix="/api/runs", tags=["sources"])


@router.get("/{run_id}/sources")
async def get_sources(run_id: str):
    try:
        run_dir = run_dir_path(RUNS_ROOT, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    try:
        run_dir = run_dir_path(RUNS_ROOT, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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

    append_event(run_dir, "source.created", {
        "source_id": source_id,
        "kind": kind,
        "stored_path": stored_path,
    })

    return {"source": source, "jobs": jobs, "artifacts": artifacts}


@router.delete("/{run_id}/sources/{source_id}")
async def delete_source(run_id: str, source_id: str):
    try:
        run_dir = run_dir_path(RUNS_ROOT, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run not found")
    removed = remove_source(run_dir, source_id, reason="user_deleted")
    if removed is None:
        raise HTTPException(status_code=404, detail="source not found")
    append_event(run_dir, "source.deleted", {"source_id": source_id})
    append_event(run_dir, "evidence.updated", {"source_id": source_id})
    return {"source_id": source_id, "deleted": True, "removed_evidence": removed["removed_evidence"]}


def _uploaded_text_preview(path: Path, limit: int = 1200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"
