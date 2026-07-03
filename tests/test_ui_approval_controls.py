"""Tests for UI-side approval artifact writers."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.pipeline.approval_gates import require_patch_approval, require_run_approval
from autoad_researcher.ui.intent_draft import load_stage3_approval, save_stage3_approval


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_ui_patch_approval_file_is_accepted_by_pipeline_gate(tmp_path: Path):
    run_dir = tmp_path / "run_ui"
    stage_dir = run_dir / "patch_applicator"
    _write_json(run_dir / "patch_planner" / "patch_planner_approval_request.json", {"ok": True})

    path = save_stage3_approval(
        run_dir,
        decision_type="patch_approval",
        confirmed_by_user=True,
        user_confirmation_text="I approve the proposed patch plan.",
    )

    assert path == run_dir / "approvals" / "patch_approval.json"
    assert "sk-" not in path.read_text(encoding="utf-8")
    assert load_stage3_approval(run_dir, decision_type="patch_approval").confirmed_by_user is True
    assert require_patch_approval("run_ui", run_dir, stage_dir).passed


def test_ui_run_approval_file_is_accepted_by_pipeline_gate(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_ui"
    stage_dir = run_dir / "runner_execute"
    _write_json(run_dir / "patch_applicator" / "patch_runner_handoff.json", {"ok": True})
    monkeypatch.setenv("AUTOAD_L3_REAL_EXECUTION_ALLOWED", "1")

    path = save_stage3_approval(
        run_dir,
        decision_type="run_approval",
        confirmed_by_user=True,
        user_confirmation_text="I approve real L3 execution.",
    )

    assert path == run_dir / "approvals" / "run_approval.json"
    assert "sk-" not in path.read_text(encoding="utf-8")
    assert load_stage3_approval(run_dir, decision_type="run_approval").confirmed_by_user is True
    assert require_run_approval("run_ui", run_dir, stage_dir).passed


def test_ui_approval_writer_rejects_api_key_like_text(tmp_path: Path):
    with pytest.raises(ValidationError, match="API-key-like"):
        save_stage3_approval(
            tmp_path / "run_ui",
            decision_type="patch_approval",
            confirmed_by_user=True,
            user_confirmation_text="sk-secret12345",
        )


def test_ui_rejected_patch_approval_blocks_gate(tmp_path: Path):
    run_dir = tmp_path / "run_ui"
    stage_dir = run_dir / "patch_applicator"
    _write_json(run_dir / "patch_planner" / "patch_planner_approval_request.json", {"ok": True})
    save_stage3_approval(
        run_dir,
        decision_type="patch_approval",
        confirmed_by_user=False,
        user_confirmation_text="reject",
    )

    result = require_patch_approval("run_ui", run_dir, stage_dir)

    assert not result.passed
    assert result.blocked_record.blocked_reason == "blocked_rejected_approval:patch_approval"
