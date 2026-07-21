from pathlib import Path

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.tools import ReportToolCall, execute_tools
from autoad_researcher.reporting.tools import _text_evidence
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.snapshot import sha256_file
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


def test_typed_tools_reject_unknown_attempt_and_evidence(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    import pytest

    with pytest.raises(ValueError, match="unknown frozen Attempt"):
        execute_tools(run_dir, report_id=report_id, calls=[ReportToolCall(name="get_metrics", arguments={"attempt_id": "attempt_missing"})])
    with pytest.raises(ValueError, match="unknown registered Evidence"):
        execute_tools(run_dir, report_id=report_id, calls=[ReportToolCall(name="resolve_evidence", arguments={"evidence_id": "evidence_missing"})])


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
