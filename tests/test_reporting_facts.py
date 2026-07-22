from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.default_narrative import build_default_narrative
from autoad_researcher.reporting.digest import build_report_digest
from autoad_researcher.reporting.evidence import _fact_refs_for_source
from autoad_researcher.reporting.facts import ExperimentReportFactsV1, assemble_facts
from autoad_researcher.reporting.facts_enrichment import enrich_facts
from autoad_researcher.reporting.inventory import _add_registered_patch_diffs, _add_registered_resource_reports
from autoad_researcher.reporting.models import ReportSnapshot
from autoad_researcher.reporting.renderer_markdown import render_markdown
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.snapshot import canonical_sha256, sha256_file
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.worker.main import _process_pending_jobs
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.execution import ResourceUsageReport
from autoad_researcher.experiment.cognitive_budget import CognitiveUsageStore, new_usage
from autoad_researcher.experiment.cost_summary import CognitiveCostSummaryBuilder


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


def test_digest_separates_engineering_execution_and_insufficient_scientific_status():
    facts = ExperimentReportFactsV1.model_validate({
        "run_id": "run_digest", "session_id": "session_digest", "research_objective": {}, "evaluation_contract": {},
        "repository_and_environment": {"status": "READY"}, "baseline": [], "candidate_and_champion": {}, "ideas": [],
        "attempts": [{"attempt_id": "attempt_000001", "outcome": {"execution_status": "COMPLETED"}}],
        "primary_metrics": [{"attempt_id": "attempt_000001", "metric": "image_auroc", "value": 0.91}],
        "guardrail_metrics": [], "validity": [], "failed_attempts": [], "non_comparable_attempts": [],
        "stop_decision": {}, "cognitive_cost_summary": {}, "compute_resource_summary": {}, "uncertainties": [], "source_refs": [],
    })

    digest = build_report_digest(report_id="report_digest", facts=facts)

    assert digest.engineering_status == "READY"
    assert digest.execution_status == "COMPLETED"
    assert digest.scientific_status == "EVIDENCE_INSUFFICIENT"
    assert digest.primary_metrics == [{"attempt_id": "attempt_000001", "metric": "image_auroc", "value": 0.91}]


def test_evidence_projection_maps_candidate_attempt_metrics_baseline_and_validity_fields():
    attempt_id = "attempt_000001"
    facts = ExperimentReportFactsV1.model_validate({
        "run_id": "run_projection", "session_id": "session_projection", "research_objective": {}, "evaluation_contract": {},
        "repository_and_environment": {},
        "baseline": [{"attempt_id": attempt_id}],
        "candidate_and_champion": {"candidates": [{"candidate_id": "candidate_000001"}]},
        "ideas": [],
        "attempts": [{"attempt_id": attempt_id, "outcome": {"metrics": {"auroc": 0.91}}, "assessment": {"evaluation_status": "COMPARABLE"}}],
        "primary_metrics": [{"attempt_id": attempt_id, "metric": "auroc", "value": 0.91}], "guardrail_metrics": [],
        "validity": [{"attempt_id": attempt_id}], "failed_attempts": [{"attempt_id": attempt_id}],
        "non_comparable_attempts": [{"attempt_id": attempt_id}], "stop_decision": {}, "cognitive_cost_summary": {},
        "compute_resource_summary": {}, "uncertainties": [], "source_refs": [],
    })

    assert _fact_refs_for_source(facts, "candidate_snapshot", "candidate_snapshot:candidate_000001", "$") == ["candidate_and_champion.candidates.0"]
    assert set(_fact_refs_for_source(facts, "experiment_attempt", f"experiment_attempt:{attempt_id}", "$")) == {
        "attempts.0", "baseline.0", "failed_attempts.0", "non_comparable_attempts.0",
    }
    assert set(_fact_refs_for_source(facts, "outcome_card", f"outcome_card:{attempt_id}", "metrics.auroc")) == {
        "attempts.0.outcome.metrics.auroc", "baseline.0.outcome.metrics.auroc",
        "failed_attempts.0.outcome.metrics.auroc", "non_comparable_attempts.0.outcome.metrics.auroc",
        "primary_metrics.0.value",
    }
    assert set(_fact_refs_for_source(facts, "scientific_assessment", f"scientific_assessment:{attempt_id}", "$")) == {
        "attempts.0.assessment", "baseline.0.assessment", "failed_attempts.0.assessment",
        "non_comparable_attempts.0.assessment", "validity.0",
    }


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


def test_resource_reports_are_projected_only_from_registered_snapshot_refs(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_resource"
    run_dir.mkdir()
    path = run_dir / "attempts" / "attempt_000001" / "resource.json"
    path.parent.mkdir(parents=True)
    report = ResourceUsageReport(
        attempt_id="attempt_000001", unit_id="unit_1", subject_type="baseline", measurement_kind="measured",
        measurement_tool="nvidia-smi", gpu_count_used=1, peak_gpu_memory_mb=10, avg_gpu_memory_mb=8,
        peak_gpu_utilization_pct=90, avg_gpu_utilization_pct=70, wall_time_seconds=7200,
        cpu_time_seconds=7000, peak_cpu_memory_mb=100,
    )
    path.write_text(report.model_dump_json(), encoding="utf-8")
    ref = ArtifactReferenceV2(artifact_id="resource_usage_report:attempt_000001:resource.json", artifact_type="resource_usage_report", locator="attempts/attempt_000001/resource.json", sha256=sha256_file(path), size_bytes=path.stat().st_size)
    snapshot = ReportSnapshot(run_id=run_dir.name, session_id="session_fixture", source_refs=[ref], frozen_control_plane={}, session_revision=0, source_inventory_sha256="a" * 64, frozen_at="2026-01-01T00:00:00+00:00")
    facts = enrich_facts(run_dir, snapshot=snapshot, facts=assemble_facts(run_dir, snapshot=snapshot))
    assert facts.compute_resource_summary["status"] == "available"
    assert facts.compute_resource_summary["total_gpu_hours"] == 2.0


def test_cognitive_summary_binds_the_usage_ledger_fingerprint(tmp_path: Path):
    store = CognitiveUsageStore()
    store.append(tmp_path, session_id="session_cost", usage=new_usage(cycle_id="cycle_1", cycle_kind="compact", role="coordinator", input_tokens=3, output_tokens=4, wall_seconds=1))
    summary = CognitiveCostSummaryBuilder(store=store).build_and_persist(tmp_path, session_id="session_cost")
    assert summary.cognitive_usage_sha256 is not None
    payload = json.loads((tmp_path / "experiments" / "cognition" / "session_cost" / "cost_summary.json").read_text(encoding="utf-8"))
    assert payload["cognitive_usage_sha256"] == summary.cognitive_usage_sha256


def test_inventory_verifies_output_manifest_hash_and_registers_handoff_patch_diffs(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_inventory"
    attempt_id = "attempt_000001"
    attempt_dir = run_dir / "attempts" / attempt_id
    attempt_dir.mkdir(parents=True)
    resource = ResourceUsageReport(
        attempt_id=attempt_id, unit_id="unit_1", subject_type="baseline", measurement_kind="measured",
        measurement_tool="nvidia-smi", gpu_count_used=1, peak_gpu_memory_mb=10, avg_gpu_memory_mb=8,
        peak_gpu_utilization_pct=90, avg_gpu_utilization_pct=70, wall_time_seconds=60,
        cpu_time_seconds=50, peak_cpu_memory_mb=100,
    )
    resource_path = attempt_dir / "resource.json"
    resource_path.write_text(resource.model_dump_json(), encoding="utf-8")
    manifest_payload = {
        "schema_version": 1,
        "outputs": [{"path": "resource.json", "sha256": sha256_file(resource_path), "size_bytes": resource_path.stat().st_size}],
    }
    manifest_payload["manifest_sha256"] = "0" * 64
    (attempt_dir / "outputs.json").write_text(json.dumps(manifest_payload), encoding="utf-8")
    (attempt_dir / "execution_result.json").write_text(json.dumps({
        "schema_version": 1, "run_id": run_dir.name, "attempt": attempt_id, "command_id": "command_1",
        "command_sha256": "a" * 64, "status": "success", "exit_code": 0, "timed_out": False,
        "stdout_path": "stdout.log", "stderr_path": "stderr.log", "output_manifest_path": "outputs.json",
    }), encoding="utf-8")
    recorded = []
    _add_registered_resource_reports(run_dir, attempt_id, lambda locator, artifact_type, artifact_id, **_kwargs: recorded.append((locator, artifact_type, artifact_id)))
    assert recorded == []

    manifest_payload["manifest_sha256"] = canonical_sha256({key: value for key, value in manifest_payload.items() if key != "manifest_sha256"})
    (attempt_dir / "outputs.json").write_text(json.dumps(manifest_payload), encoding="utf-8")
    _add_registered_resource_reports(run_dir, attempt_id, lambda locator, artifact_type, artifact_id, **_kwargs: recorded.append((locator, artifact_type, artifact_id)))
    assert recorded == [(f"attempts/{attempt_id}/resource.json", "resource_usage_report", f"resource_usage_report:{attempt_id}:resource.json")]

    (attempt_dir / "final_patch.diff").write_text("diff --git a/a b/a\n", encoding="utf-8")
    _add_registered_patch_diffs(run_dir, attempt_id, lambda locator, artifact_type, artifact_id, **_kwargs: recorded.append((locator, artifact_type, artifact_id)))
    assert recorded[-1] == (f"attempts/{attempt_id}/final_patch.diff", "patch_diff", f"patch_diff:{attempt_id}:final_patch.diff")
