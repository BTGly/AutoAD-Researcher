"""Version-bound API for immutable report artifacts."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400

router = APIRouter(prefix="/api/runs/{run_id}/reports", tags=["reports"])
_CORE_ARTIFACTS = {"report.md", "report.html", "report_facts.json", "evidence_index.json", "report_digest.json", "report_validation.json", "narrative_sections.json"}


class ReportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1)


def _store(run_id: str):
    return run_dir_or_400(RUNS_ROOT, run_id), ReportStore()


@router.get("")
async def list_reports(run_id: str, session_id: str | None = None):
    run_dir, store = _store(run_id)
    return {"reports": [item.model_dump(mode="json") for item in store.list_manifests(run_dir, session_id=session_id)]}


@router.post("")
async def create_report(run_id: str, request: ReportCreateRequest):
    run_dir, _ = _store(run_id)
    try:
        result, created = ReportRequestService().request(run_dir, session_id=request.session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"created": created, "manifest": result["manifest"].model_dump(mode="json"), "job": result["job"]}


@router.get("/latest")
async def latest_report(run_id: str, session_id: str | None = None):
    run_dir, store = _store(run_id)
    reports = store.list_manifests(run_dir, session_id=session_id)
    if not reports:
        raise HTTPException(404, "report not found")
    return reports[-1].model_dump(mode="json")


@router.get("/{report_id}/manifest")
async def get_manifest(run_id: str, report_id: str):
    run_dir, store = _store(run_id)
    try:
        return store.load_manifest(run_dir, report_id).model_dump(mode="json")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report not found") from exc


@router.get("/{report_id}/content")
async def get_content(run_id: str, report_id: str, format: str = "md"):
    if format not in {"md", "html"}:
        raise HTTPException(400, "format must be md or html")
    run_dir, store = _store(run_id)
    try:
        store.load_manifest(run_dir, report_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report not found") from exc
    filename = "report.md" if format == "md" else "report.html"
    path = run_dir / "reports" / report_id / filename
    if not path.is_file():
        raise HTTPException(409, "requested report format is not available")
    return {"report_id": report_id, "format": format, "content": path.read_text(encoding="utf-8")}


@router.get("/{report_id}/evidence/{evidence_id}")
async def get_evidence(run_id: str, report_id: str, evidence_id: str):
    run_dir, store = _store(run_id)
    try:
        store.load_manifest(run_dir, report_id)
        index = EvidenceIndex.model_validate_json((run_dir / "reports" / report_id / "evidence_index.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report evidence not found") from exc
    entry = next((item for item in index.entries if item.evidence_id == evidence_id), None)
    if entry is None:
        raise HTTPException(404, "evidence not found")
    return entry.model_dump(mode="json")


@router.get("/{report_id}/download/{artifact}")
async def download_report_artifact(run_id: str, report_id: str, artifact: str):
    if artifact not in _CORE_ARTIFACTS:
        raise HTTPException(404, "report artifact not found")
    run_dir, store = _store(run_id)
    try:
        store.load_manifest(run_dir, report_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report not found") from exc
    path = run_dir / "reports" / report_id / artifact
    if not path.is_file():
        raise HTTPException(404, "report artifact not found")
    media_type = "text/markdown" if artifact.endswith(".md") else "application/json" if artifact.endswith(".json") else "text/html"
    return FileResponse(path, media_type=media_type, filename=f"{report_id}-{artifact}")
