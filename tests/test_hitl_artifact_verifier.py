"""Tests for the Phase 2D HITL artifact verifier."""

import json
from pathlib import Path

from scripts.verify_hitl_artifacts import format_report, main, verify_hitl_artifacts


def _write(path: Path, text: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False))


def _complete_run(run_dir: Path) -> None:
    _write(run_dir / "input_task.yaml", "run_id: run_hitl\nrequest: test\n")
    _write_json(run_dir / "ui_chat" / "intent_draft.json", {"run_id": "run_hitl"})
    _write_json(run_dir / "ui_chat" / "clarification_input.json", {"input_task": {"run_id": "run_hitl"}})
    _write_json(run_dir / "approvals" / "intent_confirmation.json", {"decision": "approved"})
    _write_json(run_dir / "patch_planner" / "approval_gate_report.json", {"status": "passed"})
    _write_json(run_dir / "approvals" / "patch_approval.json", {"confirmed_by_user": True})
    _write_json(run_dir / "patch_applicator" / "approval_gate_report.json", {"status": "passed"})
    _write_json(run_dir / "approvals" / "run_approval.json", {"confirmed_by_user": True})
    _write_json(run_dir / "runner_execute" / "approval_gate_report.json", {"status": "passed"})
    _write_json(run_dir / "runner_execute" / "execution_manifest.json", {"status": "completed"})
    _write_json(run_dir / "final_report" / "final_report_facts.json", {"scientific_claim": "supported"})


def test_empty_run_is_blocked(tmp_path: Path):
    checks = verify_hitl_artifacts(tmp_path / "run_hitl")

    assert any(check.status == "BLOCKED" for check in checks)
    assert checks[0].name == "input_task.yaml"


def test_only_intent_draft_is_blocked(tmp_path: Path):
    run_dir = tmp_path / "run_hitl"
    _write_json(run_dir / "ui_chat" / "intent_draft.json", {"run_id": "run_hitl"})

    checks = verify_hitl_artifacts(run_dir)

    assert any(check.name == "intent_draft.json" and check.status == "PASS" for check in checks)
    assert any(check.name == "input_task.yaml" and check.status == "BLOCKED" for check in checks)


def test_intent_approved_without_input_task_is_blocked(tmp_path: Path):
    run_dir = tmp_path / "run_hitl"
    _write_json(run_dir / "approvals" / "intent_confirmation.json", {"decision": "approved"})

    checks = verify_hitl_artifacts(run_dir)

    assert any(check.name == "intent_confirmation approved" and check.status == "PASS" for check in checks)
    assert any(check.name == "input_task.yaml" and check.status == "BLOCKED" for check in checks)


def test_patch_approval_missing_is_blocked(tmp_path: Path):
    run_dir = tmp_path / "run_hitl"
    _complete_run(run_dir)
    (run_dir / "approvals" / "patch_approval.json").unlink()

    checks = verify_hitl_artifacts(run_dir)

    assert any(check.name == "patch_approval confirmed" and check.status == "BLOCKED" for check in checks)


def test_complete_artifacts_pass(tmp_path: Path):
    run_dir = tmp_path / "run_hitl"
    _complete_run(run_dir)

    checks = verify_hitl_artifacts(run_dir)

    assert all(check.status == "PASS" for check in checks)
    assert "status: passed" in format_report("run_hitl", checks)


def test_cli_returns_nonzero_for_blocked_run(tmp_path: Path, capsys):
    code = main(["--run-id", "run_hitl", "--runs-root", str(tmp_path)])

    assert code == 2
    assert "status: blocked" in capsys.readouterr().out
