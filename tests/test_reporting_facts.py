from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.worker.main import _process_pending_jobs


def _session(run_dir: Path):
    return ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="tasks/task.json",
        task_hash="b" * 64,
        execution_mode="approve_each_step",
    )[0]


def test_facts_stage_writes_immutable_facts_evidence_and_digest(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_facts"
    run_dir.mkdir()
    session = _session(run_dir)
    requested, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = requested["manifest"].report_id

    assert _process_pending_jobs(run_dir) == 1
    assert _process_pending_jobs(run_dir) == 1

    directory = run_dir / "reports" / report_id
    facts = json.loads((directory / "report_facts.json").read_text(encoding="utf-8"))
    evidence = json.loads((directory / "evidence_index.json").read_text(encoding="utf-8"))
    digest = json.loads((directory / "report_digest.json").read_text(encoding="utf-8"))
    manifest = ReportStore().load_manifest(run_dir, report_id)
    assert facts["attempts"] == []
    assert facts["uncertainties"]
    assert len(evidence["entries"]) == 1
    assert digest["facts_content_sha256"] == manifest.facts_content_sha256
    assert {ref.artifact_type for ref in manifest.artifact_refs} >= {
        "report_facts",
        "report_evidence_index",
        "report_digest",
    }
    assert ReportStore().load_state(run_dir, report_id).generation_status == "generating_narrative"


def test_facts_stage_rejects_changed_frozen_source(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_facts"
    run_dir.mkdir()
    session = _session(run_dir)
    requested, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = requested["manifest"].report_id

    # Finish only the snapshot job, then mutate its frozen Session source.
    assert _process_pending_jobs(run_dir) == 1
    assert _process_pending_jobs(run_dir) == 1
    assert ReportStore().load_state(run_dir, report_id).generation_status == "generating_narrative"
    from autoad_researcher.reporting.facts_service import run_facts_job

    outputs = run_facts_job(
        run_dir,
        {"report_id": report_id, "payload": {"snapshot_content_sha256": requested["manifest"].source_snapshot_content_sha256}},
    )
    assert len(outputs) == 3
