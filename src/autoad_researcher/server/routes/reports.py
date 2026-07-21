"""Version-bound API for immutable report artifacts and optional render jobs."""

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.reporting.render_request import request_optional_format
from autoad_researcher.reporting.service import ReportRequestService, retry_failed_report_job
from autoad_researcher.reporting.snapshot import sha256_file
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400

router = APIRouter(prefix="/api/runs/{run_id}/reports", tags=["reports"])
MAX_REPORT_DOWNLOAD_BYTES = 10 * 1024 * 1024
_DOWNLOAD_ARTIFACTS = {
    "report.md", "report.html", "report.pdf", "report_bundle.zip", "checksums.sha256",
    "report_facts.json", "evidence_index.json", "report_digest.json", "report_validation.json",
    "narrative_sections.json", "claim_evidence_map.json", "report_pdf_result.json", "delivery_state_snapshot.json", "bundle_exclusions.json",
}


class ReportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1)


class ReportRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^job_[0-9]+$")


def _store(run_id: str):
    return run_dir_or_400(RUNS_ROOT, run_id), ReportStore()


@router.get("")
async def list_reports(run_id: str, session_id: str | None = None):
    run_dir, store = _store(run_id)
    return {"reports": [_projection(item, store.load_state(run_dir, item.report_id)) for item in store.list_manifests(run_dir, session_id=session_id)]}


@router.post("")
async def create_report(run_id: str, request: ReportCreateRequest):
    run_dir, _ = _store(run_id)
    try:
        result, created = ReportRequestService().request(run_dir, session_id=request.session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"created": created, "manifest": _projection(result["manifest"], ReportStore().load_state(run_dir, result["manifest"].report_id)), "job": result["job"]}


@router.get("/latest-created")
async def latest_created_report(run_id: str, session_id: str | None = None):
    run_dir, store = _store(run_id)
    reports = store.list_manifests(run_dir, session_id=session_id)
    if not reports:
        raise HTTPException(404, "report not found")
    manifest = reports[-1]
    return _projection(manifest, store.load_state(run_dir, manifest.report_id))


@router.get("/latest-content-ready")
async def latest_content_ready_report(run_id: str, session_id: str | None = None):
    run_dir, store = _store(run_id)
    reports = store.list_manifests(run_dir, session_id=session_id)
    for manifest in reversed(reports):
        state = store.load_state(run_dir, manifest.report_id)
        if state.generation_status != "content_ready":
            continue
        try:
            _verified_artifact_path(run_dir, manifest.report_id, state, "report.md")
        except (FileNotFoundError, ValueError):
            continue
        return _projection(manifest, state)
    raise HTTPException(404, "content-ready report not found")


@router.get("/latest", include_in_schema=False)
async def latest_report_compat(run_id: str, session_id: str | None = None):
    """Temporary route compatibility; clients should use explicit latest semantics."""
    return await latest_content_ready_report(run_id, session_id)


@router.get("/{report_id}/manifest")
async def get_manifest(run_id: str, report_id: str):
    run_dir, store = _store(run_id)
    try:
        manifest = store.load_manifest(run_dir, report_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report not found") from exc
    return manifest.model_dump(mode="json")


@router.get("/{report_id}/state")
async def get_state(run_id: str, report_id: str):
    run_dir, store = _store(run_id)
    try:
        manifest = store.load_manifest(run_dir, report_id)
        state = store.load_state(run_dir, report_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report not found") from exc
    payload = state.model_dump(mode="json")
    payload.update(
        {
            "available_artifacts": _available_artifacts(run_dir, report_id, state),
            "jobs": [item for item in load_pipeline_jobs(run_dir) if item.get("job_id") in state.job_ids and item.get("report_id") == report_id],
        }
    )
    return payload


@router.get("/{report_id}/digest")
async def get_digest(run_id: str, report_id: str):
    run_dir, store = _store(run_id)
    try:
        manifest = store.load_manifest(run_dir, report_id)
        path = _verified_artifact_path(run_dir, report_id, store.load_state(run_dir, report_id), "report_digest.json")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(409, "report digest is not available") from exc
    return json.loads(path.read_text(encoding="utf-8"))


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


@router.post("/{report_id}/retry")
async def retry_report_job(run_id: str, report_id: str, request: ReportRetryRequest):
    run_dir, _ = _store(run_id)
    try:
        return {"job": retry_failed_report_job(run_dir, report_id=report_id, job_id=request.job_id)}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{report_id}/content")
async def get_content(run_id: str, report_id: str, format: Literal["md", "html"] = "md"):
    run_dir, store = _store(run_id)
    try:
        store.load_manifest(run_dir, report_id)
        state = store.load_state(run_dir, report_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report not found") from exc
    filename = "report.md" if format == "md" else "report.html"
    try:
        path = _verified_artifact_path(run_dir, report_id, state, filename)
    except (FileNotFoundError, ValueError):
        raise HTTPException(409, "requested report format is not available")
    return {"report_id": report_id, "format": format, "content": path.read_text(encoding="utf-8")}


@router.get("/{report_id}/evidence/{evidence_id}")
async def get_evidence(run_id: str, report_id: str, evidence_id: str):
    run_dir, store = _store(run_id)
    try:
        store.load_manifest(run_dir, report_id)
        state = store.load_state(run_dir, report_id)
        path = _verified_artifact_path(run_dir, report_id, state, "evidence_index.json")
        index = EvidenceIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report evidence not found") from exc
    entry = next((item for item in index.entries if item.evidence_id == evidence_id), None)
    if entry is None:
        raise HTTPException(404, "evidence not found")
    return entry.model_dump(mode="json")


@router.get("/{report_id}/evidence")
async def list_evidence(run_id: str, report_id: str):
    run_dir, store = _store(run_id)
    try:
        manifest = store.load_manifest(run_dir, report_id)
        state = store.load_state(run_dir, report_id)
        path = _verified_artifact_path(run_dir, report_id, state, "evidence_index.json")
        index = EvidenceIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(409, "report evidence is not available") from exc
    if index.report_id != report_id or index.snapshot_content_sha256 != manifest.source_snapshot_content_sha256:
        raise HTTPException(409, "report evidence identity conflicts with manifest")
    return {"report_id": report_id, "entries": [entry.model_dump(mode="json") for entry in index.entries]}


@router.get("/{report_id}/download/{artifact}")
async def download_report_artifact(run_id: str, report_id: str, artifact: str):
    if artifact not in _DOWNLOAD_ARTIFACTS:
        raise HTTPException(404, "report artifact not found")
    run_dir, store = _store(run_id)
    try:
        store.load_manifest(run_dir, report_id)
        state = store.load_state(run_dir, report_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report not found") from exc
    try:
        path = _verified_artifact_path(run_dir, report_id, state, artifact)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "report artifact not found")
    delivery = next((item for item in state.deliveries if Path(item.artifact_ref.locator).name == artifact), None)
    reference = next((item for item in state.artifact_refs if Path(item.locator).name == artifact), None)
    if delivery is None or reference is None or delivery.artifact_ref != reference:
        raise HTTPException(404, "report artifact delivery is not registered")
    return FileResponse(
        path,
        media_type=delivery.media_type,
        filename=delivery.download_filename,
        content_disposition_type=delivery.content_disposition_type,
    )


def _projection(manifest, state) -> dict:
    payload = manifest.model_dump(mode="json")
    payload.update(
        {
            "generation_status": state.generation_status,
            "review_status": state.review_status,
            "format_status": state.format_status.model_dump(mode="json"),
            "facts_content_sha256": state.facts_content_sha256,
        }
    )
    return payload


def _registered_names(state) -> set[str]:
    return {Path(item.locator).name for item in state.artifact_refs}


def _available_artifacts(run_dir: Path, report_id: str, state) -> list[str]:
    available: list[str] = []
    for name in _registered_names(state):
        try:
            _verified_artifact_path(run_dir, report_id, state, name)
        except (FileNotFoundError, ValueError):
            continue
        available.append(name)
    return sorted(available)


def _verified_artifact_path(run_dir: Path, report_id: str, state, name: str) -> Path:
    if name == "report_manifest.json":
        path = run_dir / "reports" / report_id / name
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(name)
        return path
    ref = next((item for item in state.artifact_refs if Path(item.locator).name == name), None)
    if ref is None:
        raise FileNotFoundError(name)
    path = run_dir / "reports" / report_id / name
    root = (run_dir / "reports" / report_id).resolve()
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("report artifact escapes its report directory")
    if path.stat().st_size > MAX_REPORT_DOWNLOAD_BYTES:
        raise ValueError("report artifact is too large for this API")
    if sha256_file(path) != ref.sha256:
        raise ValueError("report artifact checksum differs from State")
    return path
