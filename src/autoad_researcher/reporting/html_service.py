"""Render self-contained HTML after validated Markdown is content-ready."""

from pathlib import Path
from typing import Any

from autoad_researcher.reporting.content_persistence import write_immutable_report_text
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.renderer_html import render_html
from autoad_researcher.reporting.store import ReportStore

REPORT_HTML_JOB_TYPE = "report_render_html"


def run_html_job(run_dir: Path, job: dict[str, Any]) -> list[str]:
    report_id = job.get("report_id")
    if not isinstance(report_id, str):
        raise ValueError("report HTML Job lacks report identity")
    store = ReportStore()
    state = store.load_state(run_dir, report_id)
    if state.generation_status != "content_ready":
        raise ValueError("report HTML rendering requires content_ready")
    directory = run_dir / "reports" / report_id
    if state.format_status.html != "ready":
        markdown = (directory / "report.md").read_text(encoding="utf-8")
        evidence = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
        write_immutable_report_text(run_dir, report_id=report_id, filename="report.html", artifact_type="report_html", text=render_html(report_id=report_id, markdown=markdown, evidence=evidence))
        store.set_format_status(run_dir, report_id=report_id, format_name="html", status="ready")
    return [str((directory / "report.html").relative_to(run_dir))]
