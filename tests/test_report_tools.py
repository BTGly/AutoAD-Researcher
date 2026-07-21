import json
from pathlib import Path

import pytest

from autoad_researcher.environments.snapshot import EnvironmentSnapshot, environment_snapshot_sha256
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.tools import ReportToolCall, execute_tools
from autoad_researcher.reporting.tools import _text_evidence
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.snapshot import sha256_file
from autoad_researcher.reporting.tools import _execute
from autoad_researcher.reporting.verified_read import load_verified_report_facts
from autoad_researcher.worker.main import _process_pending_jobs


def _ready_report(tmp_path: Path):
    run_dir = tmp_path / "run_report_tools"
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(
        run_dir, task_ref="tasks/task.json", task_hash="b" * 64, execution_mode="approve_each_step"
    )[0]
    request, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    for _ in range(5):
        _process_pending_jobs(run_dir)
    return run_dir, request["manifest"].report_id


def _ready_report_with_environment(tmp_path: Path):
    run_dir = tmp_path / "run_report_tools_environment"
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(
        run_dir, task_ref="tasks/task.json", task_hash="c" * 64, execution_mode="approve_each_step"
    )[0]
    payload = {
        "schema_version": 1,
        "environment_kind": "python_uv_venv",
        "runtime_versions": {"python": "3.12.0"},
        "package_manager": "uv",
        "package_manager_version": "0.6.1",
        "packages": [{"name": "numpy", "version": "2.2.0", "source": "registry"}],
        "platform": "linux",
        "accelerator": {"kind": "cuda", "devices": ["GPU 0"], "runtime_version": "12.4"},
        "repository_fingerprint": "repo-fingerprint",
        "environment_path": "/private/local/environment",
        "package_inventory_sha256": "d" * 64,
        "repository_commit": "abc123",
        "validation_report_sha256": "e" * 64,
        "project_smoke_evidence": [{"validation_id": "smoke", "status": "passed"}],
    }
    payload["environment_sha256"] = environment_snapshot_sha256(payload)
    snapshot = EnvironmentSnapshot.model_validate(payload)
    path = run_dir / "environment" / "snapshot.json"
    path.parent.mkdir()
    path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    ExperimentSessionStore().update_environment_state(
        run_dir,
        session_id=session.session_id,
        status="READY_FOR_BASELINE",
        environment_status="ready",
        readiness_status="ready",
        readiness_blockers=[],
        environment_snapshot_ref="environment/snapshot.json",
    )
    request, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    for _ in range(5):
        _process_pending_jobs(run_dir)
    return run_dir, request["manifest"].report_id


def test_typed_tools_are_report_local_and_return_only_frozen_context(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    results = execute_tools(
        run_dir,
        report_id=report_id,
        calls=[
            ReportToolCall(name="get_report_digest"),
            ReportToolCall(name="get_evaluation_contract"),
            ReportToolCall(name="get_budget_usage"),
            ReportToolCall(name="list_attempts"),
        ],
    )
    assert [item["name"] for item in results] == ["get_report_digest", "get_evaluation_contract", "get_budget_usage", "list_attempts"]
    assert results[0]["result"]["report_id"] == report_id
    assert "cognitive_cost_summary" in results[2]["result"]
    assert results[0]["result"]["fact_refs"]
    assert results[0]["result"]["evidence_ids"]
    for item in results:
        assert {"status", "value", "fact_refs", "evidence_ids"}.issubset(item["result"])


def test_typed_tools_reject_unknown_attempt_and_evidence(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    with pytest.raises(ValueError, match="unknown frozen Attempt"):
        execute_tools(run_dir, report_id=report_id, calls=[ReportToolCall(name="get_metrics", arguments={"attempt_id": "attempt_missing"})])
    with pytest.raises(ValueError, match="unknown registered Evidence"):
        execute_tools(run_dir, report_id=report_id, calls=[ReportToolCall(name="resolve_evidence", arguments={"evidence_id": "evidence_missing"})])


def test_environment_tool_returns_safe_snapshot_with_registered_evidence(tmp_path: Path):
    run_dir, report_id = _ready_report_with_environment(tmp_path)
    facts = json.loads((run_dir / "reports" / report_id / "report_facts.json").read_text(encoding="utf-8"))
    projection = facts["repository_and_environment"]["environment_snapshot"]
    assert projection["status"] == "available"
    assert projection["snapshot"]["packages"] == [{"name": "numpy", "version": "2.2.0", "source": "registry"}]
    assert "environment_path" not in projection["snapshot"]

    result = execute_tools(
        run_dir,
        report_id=report_id,
        calls=[ReportToolCall(name="get_environment_snapshot")],
    )[0]["result"]

    assert result["status"] == "available"
    assert result["value"] == projection
    assert result["evidence_ids"]
    index = json.loads((run_dir / "reports" / report_id / "evidence_index.json").read_text(encoding="utf-8"))
    python_entry = next(item for item in index["entries"] if item["field_path"] == "runtime_versions.python")
    path_entry = next(item for item in index["entries"] if item["field_path"] == "environment_path")
    assert python_entry["fact_refs"] == ["repository_and_environment.environment_snapshot.snapshot.runtime_versions.python"]
    assert path_entry["fact_refs"] == []


def test_typed_tools_reject_snapshot_identity_mismatch(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)

    with pytest.raises(ValueError, match="snapshot identity conflicts with manifest"):
        execute_tools(
            run_dir,
            report_id=report_id,
            calls=[ReportToolCall(name="get_report_digest")],
            snapshot_content_sha256_expected="f" * 64,
        )


def test_verified_facts_reader_rejects_a_changed_registered_artifact(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    path = run_dir / "reports" / report_id / "report_facts.json"
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 no longer matches"):
        load_verified_report_facts(run_dir, report_id=report_id)


def test_patch_diff_without_registered_artifact_is_explicitly_unavailable(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)

    result = execute_tools(run_dir, report_id=report_id, calls=[ReportToolCall(name="get_patch_diff")])[0]["result"]

    assert result == {
        "status": "unavailable",
        "value": None,
        "reason": "no unambiguous registered patch artifact is available",
        "fact_refs": [],
        "evidence_ids": [],
    }


def test_attempt_deep_reads_return_their_fact_and_evidence_provenance(tmp_path: Path):
    facts = ExperimentReportFactsV1.model_validate({
        "run_id": "run_tools", "session_id": "session_tools",
        "research_objective": {}, "evaluation_contract": {}, "repository_and_environment": {},
        "baseline": [], "candidate_and_champion": {}, "ideas": [],
        "attempts": [{
            "attempt_id": "attempt_000001",
            "outcome": {"metrics": {"auroc": 0.91}},
            "assessment": {"scientific_effect": "IMPROVEMENT"},
            "attempt_metrics": {"auroc": 0.91},
        }],
        "primary_metrics": [], "guardrail_metrics": [], "validity": [],
        "failed_attempts": [], "non_comparable_attempts": [], "stop_decision": {},
        "cognitive_cost_summary": {}, "compute_resource_summary": {}, "uncertainties": [], "source_refs": [],
    })
    evidence = EvidenceIndex.model_validate({
        "report_id": "report_tools", "snapshot_content_sha256": "a" * 64,
        "entries": [
            _evidence_entry("evidence_outcome", "attempts.0.outcome"),
            _evidence_entry("evidence_assessment", "attempts.0.assessment"),
            _evidence_entry("evidence_metrics", "attempts.0.attempt_metrics"),
        ],
    })

    for name, fact_ref, evidence_id in (
        ("get_outcome_card", "attempts.0.outcome", "evidence_outcome"),
        ("get_scientific_assessment", "attempts.0.assessment", "evidence_assessment"),
        ("get_metrics", "attempts.0.attempt_metrics", "evidence_metrics"),
    ):
        result = _execute(tmp_path, ReportToolCall(name=name, arguments={"attempt_id": "attempt_000001"}), facts, evidence, None, "")
        assert {"status", "value", "fact_refs", "evidence_ids"}.issubset(result)
        assert fact_ref in result["fact_refs"]
        assert evidence_id in result["evidence_ids"]


def _evidence_entry(evidence_id: str, fact_ref: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_kind": "outcome_card",
        "source_object_id": evidence_id,
        "artifact_ref": {
            "artifact_id": evidence_id,
            "artifact_type": "outcome_card",
            "locator": f"artifacts/{evidence_id}.json",
            "sha256": "a" * 64,
            "size_bytes": 1,
        },
        "field_path": "$",
        "fact_refs": [fact_ref],
        "attempt_id": "attempt_000001",
        "summary": evidence_id,
    }


def test_log_tool_reads_only_sha_bound_registered_log(tmp_path: Path):
    path = tmp_path / "attempts" / "attempt_000001" / "stdout.log"
    path.parent.mkdir(parents=True)
    path.write_text("first\nneedle\nthird\n", encoding="utf-8")
    index = EvidenceIndex.model_validate({
        "report_id": "report_tools", "snapshot_content_sha256": "a" * 64,
        "entries": [{
            "evidence_id": "evidence_log", "evidence_kind": "attempt_stdout_log", "source_object_id": "attempt_stdout_log:attempt_000001",
            "artifact_ref": {"artifact_id": "attempt_stdout_log:attempt_000001", "artifact_type": "attempt_stdout_log", "locator": "attempts/attempt_000001/stdout.log", "sha256": sha256_file(path), "size_bytes": path.stat().st_size},
            "field_path": "$", "attempt_id": "attempt_000001", "summary": "registered stdout",
        }],
    })
    found = _text_evidence(tmp_path, index, {"attempt_id": "attempt_000001", "stream": "stdout"}, expected=("log",), query="needle", start=None, end=None)
    assert found["matches"] == [{"line": 2, "text": "needle"}]
    assert found["evidence_ids"] == ["evidence_log"]
    assert {"status", "value", "fact_refs", "evidence_ids"}.issubset(found)
