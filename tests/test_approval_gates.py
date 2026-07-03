"""Tests for Phase 2C approval gate helpers."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoad_researcher.pipeline.approval_gates import (
    require_intent_confirmation,
    require_patch_approval,
    require_run_approval,
)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _intent_draft(run_dir: Path, run_id: str = "run_gate") -> None:
    _write_json(run_dir / "ui_chat" / "intent_draft.json", {
        "run_id": run_id,
        "source": "ui_chat",
        "research_goal": "Reduce memory.",
        "problem_type": "resource_efficiency",
        "primary_metrics": ["peak_gpu_memory_mb"],
        "guardrail_metrics": ["instance_auroc"],
        "allowed_change_scope": ["patchcore/sampler.py"],
        "forbidden_change_scope": ["configs/"],
        "benchmark_scope": {},
        "success_criteria": "memory decreases without AUROC regression",
        "risks": [],
        "open_questions": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def _intent_confirmation(run_dir: Path, decision: str = "approved", run_id: str = "run_gate") -> None:
    _write_json(run_dir / "approvals" / "intent_confirmation.json", {
        "run_id": run_id,
        "checkpoint": "intent_confirmation",
        "decision": decision,
        "reviewer": "local_user",
        "source_artifact": "ui_chat/intent_draft.json",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def _stage3_approval(run_dir: Path, filename: str, decision_type: str, confirmed: bool, run_id: str = "run_gate") -> None:
    _write_json(run_dir / "approvals" / filename, {
        "run_id": run_id,
        "decision_type": decision_type,
        "confirmed_by_user": confirmed,
        "user_confirmation_text": "approved",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evidence_kind": "approval_artifact",
    })


def test_intent_gate_missing_confirmation_blocks(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "patch_planner"

    result = require_intent_confirmation("run_gate", run_dir, stage_dir)

    assert not result.passed
    assert result.blocked_record.blocked_reason == "blocked_missing_approval:intent_confirmation"
    assert (stage_dir / "approval_gate_report.json").is_file()


def test_intent_gate_rejected_and_revision_block(tmp_path: Path):
    for decision, reason in [
        ("rejected", "blocked_rejected_approval:intent_confirmation"),
        ("needs_revision", "blocked_revision_required:intent_confirmation"),
    ]:
        run_dir = tmp_path / decision / "run_gate"
        stage_dir = run_dir / "patch_planner"
        _intent_draft(run_dir)
        _intent_confirmation(run_dir, decision=decision)

        result = require_intent_confirmation("run_gate", run_dir, stage_dir)

        assert not result.passed
        assert result.blocked_record.blocked_reason == reason


def test_intent_gate_approved_passes(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "patch_planner"
    _intent_draft(run_dir)
    _intent_confirmation(run_dir)

    result = require_intent_confirmation("run_gate", run_dir, stage_dir)

    assert result.passed
    assert result.report.status == "passed"
    assert result.report.observed_artifact_sha256 is not None


def test_intent_gate_run_id_mismatch_blocks(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "patch_planner"
    _intent_draft(run_dir, run_id="other")
    _intent_confirmation(run_dir)

    result = require_intent_confirmation("run_gate", run_dir, stage_dir)

    assert not result.passed
    assert result.blocked_record.blocked_reason == "blocked_invalid_approval:intent_confirmation"


def test_patch_gate_missing_false_wrong_and_valid(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "patch_applicator"
    _write_json(run_dir / "patch_planner" / "patch_planner_approval_request.json", {"ok": True})

    missing = require_patch_approval("run_gate", run_dir, stage_dir)
    assert missing.blocked_record.blocked_reason == "blocked_missing_approval:patch_approval"

    _stage3_approval(run_dir, "patch_approval.json", "patch_approval", False)
    rejected = require_patch_approval("run_gate", run_dir, stage_dir)
    assert rejected.blocked_record.blocked_reason == "blocked_rejected_approval:patch_approval"

    _stage3_approval(run_dir, "patch_approval.json", "run_approval", True)
    wrong = require_patch_approval("run_gate", run_dir, stage_dir)
    assert wrong.blocked_record.blocked_reason == "blocked_invalid_approval:patch_approval"

    _stage3_approval(run_dir, "patch_approval.json", "patch_approval", True)
    passed = require_patch_approval("run_gate", run_dir, stage_dir)
    assert passed.passed


def test_patch_gate_requires_approval_request_artifact(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "patch_applicator"
    _stage3_approval(run_dir, "patch_approval.json", "patch_approval", True)

    result = require_patch_approval("run_gate", run_dir, stage_dir)

    assert not result.passed
    assert result.blocked_record.blocked_reason == "blocked_missing_artifact:patch_planner_approval_request.json"


def test_run_gate_missing_false_wrong_env_and_valid(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "runner_execute"
    _write_json(run_dir / "patch_applicator" / "patch_runner_handoff.json", {"ok": True})

    missing = require_run_approval("run_gate", run_dir, stage_dir)
    assert missing.blocked_record.blocked_reason == "blocked_missing_approval:run_approval"

    _stage3_approval(run_dir, "run_approval.json", "run_approval", False)
    rejected = require_run_approval("run_gate", run_dir, stage_dir)
    assert rejected.blocked_record.blocked_reason == "blocked_rejected_approval:run_approval"

    _stage3_approval(run_dir, "run_approval.json", "patch_approval", True)
    wrong = require_run_approval("run_gate", run_dir, stage_dir)
    assert wrong.blocked_record.blocked_reason == "blocked_invalid_approval:run_approval"

    _stage3_approval(run_dir, "run_approval.json", "run_approval", True)
    monkeypatch.delenv("AUTOAD_L3_REAL_EXECUTION_ALLOWED", raising=False)
    no_env = require_run_approval("run_gate", run_dir, stage_dir)
    assert no_env.blocked_record.blocked_reason == "blocked_real_execution_not_allowed:run_approval"

    monkeypatch.setenv("AUTOAD_L3_REAL_EXECUTION_ALLOWED", "1")
    passed = require_run_approval("run_gate", run_dir, stage_dir)
    assert passed.passed


def test_gate_blocks_secret_like_approval(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "patch_planner"
    _intent_draft(run_dir)
    path = run_dir / "approvals" / "intent_confirmation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"run_id":"run_gate","checkpoint":"intent_confirmation","decision":"approved","reviewer":"sk-secret12345","source_artifact":"ui_chat/intent_draft.json","created_at":"2026-01-01T00:00:00Z"}', encoding="utf-8")

    result = require_intent_confirmation("run_gate", run_dir, stage_dir)

    assert not result.passed
    assert result.blocked_record.blocked_reason == "blocked_invalid_approval:intent_confirmation"
