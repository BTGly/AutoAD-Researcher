"""Version-bound API for immutable report artifacts and optional render jobs."""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.render_request import request_optional_format
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.snapshot import sha256_file
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400

router = APIRouter(prefix="/api/runs/{run_id}/reports", tags=["reports"])
_DOWNLOAD_ARTIFACTS = {
    "report.md", "report.html", "report.pdf", "report_bundle.zip", "checksums.sha256",
    "report_facts.json", "evidence_index.json", "report_digest.json", "report_validation.json",
    "narrative_sections.json", "report_pdf_result.json", "report_manifest.json",
}
_MIME_TYPES = {
    "report.md": "text/markdown", "report.html": "text/html", "report.pdf": "application/pdf",
    "report_bundle.zip": "application/zip", "checksums.sha256": "text/plain",
}


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
        manifest = store.load_manifest(run_dir, report_id)
        state = store.load_state(run_dir, report_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report not found") from exc
    payload = manifest.model_dump(mode="json")
    payload.update({"jobs": state.job_ids, "retry_count": state.retry_count, "last_error": state.last_error, "available_artifacts": _available_artifacts(run_dir, report_id, manifest)})
    return payload


@router.post("/{report_id}/render/{format_name}")
async def request_render(run_id: str, report_id: str, format_name: Literal["pdf", "bundle"]):
    run_dir, _ = _store(run_id)
    try:
        job, created = request_optional_format(run_dir, report_id=report_id, format_name=format_name)
        return {"created": created, "job": job}
    except FileNotFoundError as exc:
        raise HTTPException(404, "report not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{report_id}/content")
async def get_content(run_id: str, report_id: str, format: Literal["md", "html"] = "md"):
    run_dir, store = _store(run_id)
    try:
        manifest = store.load_manifest(run_dir, report_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report not found") from exc
    filename = "report.md" if format == "md" else "report.html"
    path = run_dir / "reports" / report_id / filename
    if filename not in _registered_names(manifest) or not path.is_file():
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
    if artifact not in _DOWNLOAD_ARTIFACTS:
        raise HTTPException(404, "report artifact not found")
    run_dir, store = _store(run_id)
    try:
        manifest = store.load_manifest(run_dir, report_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report not found") from exc
    path = run_dir / "reports" / report_id / artifact
    if artifact != "report_manifest.json":
        ref = next((item for item in manifest.artifact_refs if Path(item.locator).name == artifact), None)
        if ref is None or not path.is_file() or sha256_file(path) != ref.sha256:
            raise HTTPException(404, "report artifact not found")
    elif not path.is_file():
        raise HTTPException(404, "report artifact not found")
    media_type = _MIME_TYPES.get(artifact, "application/json")
    return FileResponse(path, media_type=media_type, filename=f"{report_id}-{artifact}")


def _registered_names(manifest) -> set[str]:
    return {Path(item.locator).name for item in manifest.artifact_refs}


def _available_artifacts(run_dir: Path, report_id: str, manifest) -> list[str]:
    return sorted(name for name in _registered_names(manifest) if (run_dir / "reports" / report_id / name).is_file())
