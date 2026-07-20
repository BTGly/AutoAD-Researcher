"""Thin reporting control-plane adapter over the existing job and event stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.job_service import create_or_get_pipeline_job
from autoad_researcher.reporting.snapshot import build_report_snapshot, snapshot_content_sha256
from autoad_researcher.reporting.store import MANIFEST_FILE, SNAPSHOT_FILE, ReportStore

REPORT_SNAPSHOT_JOB_TYPE = "report_snapshot_build"


class ReportRequestService:
    """Allocate one frozen report version and its durable first-stage Job."""

    def __init__(self, *, store: ReportStore | None = None) -> None:
        self._store = store or ReportStore()

    def request(self, run_dir: Path, *, session_id: str) -> tuple[dict[str, Any], bool]:
        snapshot = build_report_snapshot(run_dir, session_id=session_id)
        manifest, created = self._store.create_or_get(run_dir, snapshot=snapshot)
        job, job_created = create_or_get_pipeline_job(
            run_dir,
            source_id="",
            report_id=manifest.report_id,
            job_type=REPORT_SNAPSHOT_JOB_TYPE,
            idempotency_key=f"report:{session_id}:{snapshot_content_sha256(snapshot)}:{REPORT_SNAPSHOT_JOB_TYPE}",
            evidence_role="report_artifact",
            payload={
                "report_id": manifest.report_id,
                "session_id": session_id,
                "snapshot_content_sha256": manifest.source_snapshot_content_sha256,
            },
        )
        self._store.record_job(run_dir, report_id=manifest.report_id, job_id=job["job_id"])
        if created or job_created:
            append_event(
                run_dir,
                "report.queued",
                {"report_id": manifest.report_id, "session_id": session_id, "job_id": job["job_id"]},
            )
        return {"manifest": manifest, "job": job}, created or job_created


def run_snapshot_job(run_dir: Path, job: dict[str, Any]) -> list[str]:
    """Verify the frozen input before later phases assemble Facts."""

    report_id = job.get("report_id")
    payload = job.get("payload")
    if not isinstance(report_id, str) or not isinstance(payload, dict):
        raise ValueError("report snapshot Job lacks report identity")
    store = ReportStore()
    manifest = store.load_manifest(run_dir, report_id)
    if payload.get("snapshot_content_sha256") != manifest.source_snapshot_content_sha256:
        raise ValueError("report snapshot Job identity conflicts with manifest")
    snapshot = store.load_snapshot(run_dir, report_id)
    if snapshot_content_sha256(snapshot) != manifest.source_snapshot_content_sha256:
        raise ValueError("persisted report snapshot hash mismatch")
    state = store.load_state(run_dir, report_id)
    if state.generation_status == "queued":
        store.transition_generation(run_dir, report_id=report_id, target="building_snapshot")
        state = store.load_state(run_dir, report_id)
    if state.generation_status == "building_snapshot":
        store.transition_generation(run_dir, report_id=report_id, target="assembling_facts")
    elif state.generation_status not in {"assembling_facts", "content_ready"}:
        raise ValueError("report cannot build snapshot from its current state")
    append_event(run_dir, "report.snapshot_built", {"report_id": report_id})
    report_dir = run_dir / "reports" / report_id
    return [str((report_dir / SNAPSHOT_FILE).relative_to(run_dir)), str((report_dir / MANIFEST_FILE).relative_to(run_dir))]


def mark_report_job_failed(run_dir: Path, job: dict[str, Any], error: str) -> None:
    report_id = job.get("report_id")
    if isinstance(report_id, str) and report_id:
        ReportStore().mark_failed(run_dir, report_id=report_id, error=error)
        append_event(run_dir, "report.failed", {"report_id": report_id, "error": error[:500]})
