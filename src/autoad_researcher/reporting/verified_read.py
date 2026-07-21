"""Read immutable report artifacts only after their registered hashes verify."""

from __future__ import annotations

from pathlib import Path

from autoad_researcher.reporting.facts import ExperimentReportFactsV1, facts_content_sha256
from autoad_researcher.reporting.snapshot import resolve_run_relative_file, sha256_file
from autoad_researcher.reporting.store import ReportStore


def load_verified_report_facts(run_dir: Path, *, report_id: str) -> ExperimentReportFactsV1:
    """Return the frozen Facts artifact bound by the report State."""

    state = ReportStore().load_state(run_dir, report_id)
    reference = next(
        (
            item
            for item in state.artifact_refs
            if item.artifact_type == "report_facts"
            and item.artifact_id == f"report_artifact:{report_id}:report_facts.json"
        ),
        None,
    )
    if reference is None:
        raise ValueError("report Facts artifact is not registered in report State")
    path = resolve_run_relative_file(run_dir, reference.locator)
    if sha256_file(path) != reference.sha256:
        raise ValueError("report Facts artifact SHA-256 no longer matches report State")
    facts = ExperimentReportFactsV1.model_validate_json(path.read_text(encoding="utf-8"))
    if state.facts_content_sha256 != facts_content_sha256(facts):
        raise ValueError("report Facts canonical hash no longer matches report State")
    return facts
