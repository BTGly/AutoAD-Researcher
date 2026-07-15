from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.pipeline.approval_gates import require_patch_approval, require_run_approval


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _approval(run_dir: Path, filename: str, decision_type: str, confirmed: bool) -> None:
    _write_json(run_dir / "approvals" / filename, {
        "run_id": "run_gate",
        "decision_type": decision_type,
        "confirmed_by_user": confirmed,
        "user_confirmation_text": "approved",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evidence_kind": "approval_artifact",
    })


def test_patch_gate_missing_false_wrong_and_valid(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "patch_applicator"
    _write_json(run_dir / "patch_planner" / "patch_planner_approval_request.json", {"ok": True})

    assert require_patch_approval("run_gate", run_dir, stage_dir).blocked_record.blocked_reason == "blocked_missing_approval:patch_approval"
    _approval(run_dir, "patch_approval.json", "patch_approval", False)
    assert require_patch_approval("run_gate", run_dir, stage_dir).blocked_record.blocked_reason == "blocked_rejected_approval:patch_approval"
    _approval(run_dir, "patch_approval.json", "run_approval", True)
    assert require_patch_approval("run_gate", run_dir, stage_dir).blocked_record.blocked_reason == "blocked_invalid_approval:patch_approval"
    _approval(run_dir, "patch_approval.json", "patch_approval", True)
    assert require_patch_approval("run_gate", run_dir, stage_dir).passed


def test_patch_gate_requires_approval_request_artifact(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    _approval(run_dir, "patch_approval.json", "patch_approval", True)

    result = require_patch_approval("run_gate", run_dir, run_dir / "patch_applicator")

    assert result.blocked_record.blocked_reason == "blocked_missing_artifact:patch_planner_approval_request.json"


def test_run_gate_missing_false_wrong_env_and_valid(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "runner_execute"
    _write_json(run_dir / "patch_applicator" / "patch_runner_handoff.json", {"ok": True})

    assert require_run_approval("run_gate", run_dir, stage_dir).blocked_record.blocked_reason == "blocked_missing_approval:run_approval"
    _approval(run_dir, "run_approval.json", "run_approval", False)
    assert require_run_approval("run_gate", run_dir, stage_dir).blocked_record.blocked_reason == "blocked_rejected_approval:run_approval"
    _approval(run_dir, "run_approval.json", "patch_approval", True)
    assert require_run_approval("run_gate", run_dir, stage_dir).blocked_record.blocked_reason == "blocked_invalid_approval:run_approval"
    _approval(run_dir, "run_approval.json", "run_approval", True)
    monkeypatch.delenv("AUTOAD_L3_REAL_EXECUTION_ALLOWED", raising=False)
    assert require_run_approval("run_gate", run_dir, stage_dir).blocked_record.blocked_reason == "blocked_real_execution_not_allowed:run_approval"
    monkeypatch.setenv("AUTOAD_L3_REAL_EXECUTION_ALLOWED", "1")
    assert require_run_approval("run_gate", run_dir, stage_dir).passed


def test_patch_gate_rejects_secret_like_approval(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    _write_json(run_dir / "patch_planner" / "patch_planner_approval_request.json", {"ok": True})
    _approval(run_dir, "patch_approval.json", "patch_approval", True)
    path = run_dir / "approvals" / "patch_approval.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["user_confirmation_text"] = "sk-secret12345"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = require_patch_approval("run_gate", run_dir, run_dir / "patch_applicator")

    assert result.blocked_record.blocked_reason == "blocked_invalid_approval:patch_approval"
