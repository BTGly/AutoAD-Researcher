"""Validate frozen report content and publish Markdown before format rendering."""

from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.reporting.content_persistence import write_immutable_report_text
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeSectionsV1
from autoad_researcher.reporting.persistence import write_immutable_report_json
from autoad_researcher.reporting.renderer_markdown import render_markdown
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.reporting.validator import validate_report

REPORT_VALIDATE_JOB_TYPE = "report_validate"


def run_validate_job(run_dir: Path, job: dict[str, Any]) -> list[str]:
    report_id = job.get("report_id")
    if not isinstance(report_id, str):
        raise ValueError("report Validate Job lacks report identity")
    store = ReportStore()
    state = store.load_state(run_dir, report_id)
    if state.generation_status == "content_ready":
        return _outputs(run_dir, report_id)
    if state.generation_status != "validating":
        raise ValueError("report cannot validate from its current state")
    directory = run_dir / "reports" / report_id
    facts = ExperimentReportFactsV1.model_validate_json((directory / "report_facts.json").read_text(encoding="utf-8"))
    evidence = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
    narrative = NarrativeSectionsV1.model_validate_json((directory / "narrative_sections.json").read_text(encoding="utf-8"))
    validation = validate_report(facts=facts, evidence=evidence, narrative=narrative)
    write_immutable_report_json(run_dir, report_id=report_id, filename="report_validation.json", artifact_type="report_validation", value=validation.model_dump(mode="json"))
    claim_map = {
        "schema_version": 1,
        "claims": [item.model_dump(mode="json") for item in narrative.claims],
    }
    write_immutable_report_json(run_dir, report_id=report_id, filename="claim_evidence_map.json", artifact_type="report_claim_evidence_map", value=claim_map)
    if not validation.passed:
        raise ValueError("report validation failed: " + "; ".join(validation.errors))
    write_immutable_report_text(run_dir, report_id=report_id, filename="report.md", artifact_type="report_markdown", text=render_markdown(facts=facts, narrative=narrative))
    store.set_format_status(run_dir, report_id=report_id, format_name="markdown", status="ready")
    store.transition_generation(run_dir, report_id=report_id, target="content_ready")
    append_event(run_dir, "report.content_ready", {"report_id": report_id})
    return _outputs(run_dir, report_id)


def _outputs(run_dir: Path, report_id: str) -> list[str]:
    directory = run_dir / "reports" / report_id
    return [str((directory / name).relative_to(run_dir)) for name in ("claim_evidence_map.json", "report_validation.json", "report.md")]
