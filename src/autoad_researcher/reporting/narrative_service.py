"""Generate, validate and publish deterministic report content."""

import json
from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.reporting.content_persistence import write_immutable_report_text
from autoad_researcher.reporting.default_narrative import build_default_narrative
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.persistence import write_immutable_report_json
from autoad_researcher.reporting.renderer_markdown import render_markdown
from autoad_researcher.reporting.renderer_html import render_html
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.reporting.validator import validate_report

REPORT_NARRATIVE_JOB_TYPE = "report_narrative_generate"


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
    evidence = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
    narrative = build_default_narrative(facts)
    validation = validate_report(facts=facts, evidence=evidence, narrative=narrative)
    write_immutable_report_json(run_dir, report_id=report_id, filename="narrative_sections.json", artifact_type="report_narrative", value=narrative.model_dump(mode="json"))
    write_immutable_report_json(run_dir, report_id=report_id, filename="report_validation.json", artifact_type="report_validation", value=validation.model_dump(mode="json"))
    if not validation.passed:
        raise ValueError("report validation failed: " + "; ".join(validation.errors))
    markdown = render_markdown(facts=facts, narrative=narrative)
    write_immutable_report_text(run_dir, report_id=report_id, filename="report.md", artifact_type="report_markdown", text=markdown)
    html = render_html(report_id=report_id, markdown=markdown)
    write_immutable_report_text(run_dir, report_id=report_id, filename="report.html", artifact_type="report_html", text=html)
    store.set_format_status(run_dir, report_id=report_id, format_name="markdown", status="ready")
    store.set_format_status(run_dir, report_id=report_id, format_name="html", status="ready")
    store.transition_generation(run_dir, report_id=report_id, target="validating")
    store.transition_generation(run_dir, report_id=report_id, target="content_ready")
    append_event(run_dir, "report.content_ready", {"report_id": report_id})
    return _outputs(run_dir, report_id)


def _outputs(run_dir: Path, report_id: str) -> list[str]:
    directory = run_dir / "reports" / report_id
    return [str((directory / name).relative_to(run_dir)) for name in ("narrative_sections.json", "report_validation.json", "report.md", "report.html")]
