"""Queue optional report renderers without executing them in an API process."""

from pathlib import Path
from typing import Literal

from autoad_researcher.assistant.v2.job_service import create_or_get_pipeline_job
from autoad_researcher.reporting.store import ReportStore

OptionalFormat = Literal["pdf", "bundle"]
_JOB_TYPES = {"pdf": "report_render_pdf", "bundle": "report_package"}


def request_optional_format(run_dir: Path, *, report_id: str, format_name: OptionalFormat) -> tuple[dict[str, object], bool]:
    store = ReportStore()
    manifest = store.load_manifest(run_dir, report_id)
    state = store.load_state(run_dir, report_id)
    if state.generation_status != "content_ready":
        raise ValueError("optional report rendering requires content_ready")
    job_type = _JOB_TYPES[format_name]
    if format_name == "bundle" and state.format_status.html != "ready":
        raise ValueError("report bundle requires HTML readiness")
    if getattr(state.format_status, format_name) == "missing":
        store.set_format_status(run_dir, report_id=report_id, format_name=format_name, status="queued")
    job, created = create_or_get_pipeline_job(
        run_dir,
        source_id="",
        report_id=report_id,
        job_type=job_type,
        idempotency_key=f"report:{report_id}:{manifest.source_snapshot_content_sha256}:{job_type}",
        evidence_role="report_artifact",
        payload={"report_id": report_id, "snapshot_content_sha256": manifest.source_snapshot_content_sha256, "report_recipe_hash": manifest.report_recipe_hash},
    )
    store.record_job(run_dir, report_id=report_id, job_id=str(job["job_id"]))
    return job, created
