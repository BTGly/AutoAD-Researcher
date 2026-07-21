"""Generate, validate and publish deterministic report content."""

from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.reporting.default_narrative import build_default_narrative
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.persistence import write_immutable_report_json
from autoad_researcher.reporting.store import ReportStore

REPORT_NARRATIVE_JOB_TYPE = "report_narrative_generate"
REPORT_VALIDATE_JOB_TYPE = "report_validate"


def run_narrative_job(run_dir: Path, job: dict[str, Any]) -> list[str]:
    report_id = job.get("report_id")
    if not isinstance(report_id, str):
        raise ValueError("report Narrative Job lacks report identity")
    store = ReportStore()
    state = store.load_state(run_dir, report_id)
    if state.generation_status == "content_ready":
        return _outputs(run_dir, report_id)
    if state.generation_status != "generating_narrative":
        raise ValueError("report cannot generate Narrative from its current state")
    directory = run_dir / "reports" / report_id
    facts = ExperimentReportFactsV1.model_validate_json((directory / "report_facts.json").read_text(encoding="utf-8"))
    narrative = build_default_narrative(facts)
    write_immutable_report_json(run_dir, report_id=report_id, filename="narrative_sections.json", artifact_type="report_narrative", value=narrative.model_dump(mode="json"))
    store.transition_generation(run_dir, report_id=report_id, target="validating")
    from autoad_researcher.assistant.v2.job_service import create_or_get_pipeline_job

    manifest = store.load_manifest(run_dir, report_id)
    validate_job, _ = create_or_get_pipeline_job(
        run_dir,
        source_id="",
        report_id=report_id,
        job_type=REPORT_VALIDATE_JOB_TYPE,
        idempotency_key=f"report:{report_id}:{manifest.source_snapshot_content_sha256}:{REPORT_VALIDATE_JOB_TYPE}",
        evidence_role="report_artifact",
        payload={"report_id": report_id, "snapshot_content_sha256": manifest.source_snapshot_content_sha256, "report_recipe_hash": manifest.report_recipe_hash},
    )
    store.record_job(run_dir, report_id=report_id, job_id=str(validate_job["job_id"]))
    append_event(run_dir, "report.narrative_generated", {"report_id": report_id})
    return [str((directory / "narrative_sections.json").relative_to(run_dir))]


def _outputs(run_dir: Path, report_id: str) -> list[str]:
    directory = run_dir / "reports" / report_id
    return [str((directory / name).relative_to(run_dir)) for name in ("narrative_sections.json", "report_validation.json", "report.md")]
