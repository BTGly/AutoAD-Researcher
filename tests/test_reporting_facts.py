from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.default_narrative import build_default_narrative
from autoad_researcher.reporting.facts import assemble_facts
from autoad_researcher.reporting.models import ReportSnapshot
from autoad_researcher.reporting.renderer_markdown import render_markdown
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.snapshot import sha256_file
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.worker.main import _process_pending_jobs
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2


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

    directory = run_dir / "reports" / report_id
    facts = json.loads((directory / "report_facts.json").read_text(encoding="utf-8"))
    evidence = json.loads((directory / "evidence_index.json").read_text(encoding="utf-8"))
    digest = json.loads((directory / "report_digest.json").read_text(encoding="utf-8"))
    state = ReportStore().load_state(run_dir, report_id)
    assert facts["attempts"] == []
    assert facts["uncertainties"]
    assert facts["cognitive_cost_summary"]["status"] == "unknown"
    assert facts["compute_resource_summary"]["status"] == "unknown"
    assert any(item["field_path"] == "$" for item in evidence["entries"])
    assert any(item["field_path"] == "task_ref" for item in evidence["entries"])
    assert digest["facts_content_sha256"] == state.facts_content_sha256
    assert {ref.artifact_type for ref in state.artifact_refs} >= {
        "report_facts",
        "report_evidence_index",
        "report_digest",
    }
    assert ReportStore().load_state(run_dir, report_id).generation_status == "generating_narrative"


def test_facts_stage_uses_frozen_control_plane_after_live_session_changes(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_facts"
    run_dir.mkdir()
    session = _session(run_dir)
    requested, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = requested["manifest"].report_id

    # The Snapshot identity is frozen before the Facts Job is queued.
    session_path = run_dir / "experiments" / "sessions" / f"{session.session_id}.json"
    session_path.write_text('{"changed": true}\n', encoding="utf-8")

    assert _process_pending_jobs(run_dir) == 1
    assert ReportStore().load_state(run_dir, report_id).generation_status == "generating_narrative"


def test_execution_result_is_sha_bound_and_metrics_render_as_values(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_execution"
    run_dir.mkdir()
    attempt_dir = run_dir / "attempts" / "attempt_000001"
    attempt_dir.mkdir(parents=True)
    execution_path = attempt_dir / "execution_result.json"
    execution_path.write_text(json.dumps({"status": "success", "exit_code": 0}), encoding="utf-8")
    outcome_path = attempt_dir / "outcome_card.json"
    outcome_path.write_text(json.dumps({
        "attempt_id": "attempt_000001", "runtime_status": "COMPLETED", "attempt_category": "scientifically_evaluable",
        "execution_result_ref": "execution_result.json", "metrics": {"auroc": 0.91}, "metrics_parsed": True,
        "protocol_intact": True, "protocol_valid": True, "execution_status": "COMPLETED",
        "evaluation_status": "COMPARABLE", "scientific_effect": "IMPROVEMENT", "primary_delta": 0.03,
    }), encoding="utf-8")
    refs = [
        ArtifactReferenceV2(artifact_id="execution_result:attempt_000001", artifact_type="execution_result", locator="attempts/attempt_000001/execution_result.json", sha256=sha256_file(execution_path), size_bytes=execution_path.stat().st_size),
        ArtifactReferenceV2(artifact_id="outcome_card:attempt_000001", artifact_type="outcome_card", locator="attempts/attempt_000001/outcome_card.json", sha256=sha256_file(outcome_path), size_bytes=outcome_path.stat().st_size),
    ]
    snapshot = ReportSnapshot(
        run_id=run_dir.name, session_id="session_fixture", source_refs=refs,
        frozen_control_plane={"experiment_attempt": [{"attempt_id": "attempt_000001", "attempt_purpose": "baseline", "runtime_status": "COMPLETED", "execution_result_ref": "attempts/attempt_000001/execution_result.json"}]},
        session_revision=0, source_inventory_sha256="a" * 64, frozen_at="2026-01-01T00:00:00+00:00",
    )
    facts = assemble_facts(run_dir, snapshot=snapshot)
    attempt = facts.attempts[0]
    assert attempt["execution_result_binding"]["status"] == "bound"
    assert attempt["execution_result_binding"]["artifact_ref"]["sha256"] == refs[0].sha256

    markdown = render_markdown(
        facts=facts.model_copy(update={"primary_metrics": [{"attempt_id": "attempt_000001", "metric": "auroc", "value": 0.91}]}),
        narrative=build_default_narrative(facts),
    )
    assert "| attempt_000001 | auroc | 0.91 |" in markdown
