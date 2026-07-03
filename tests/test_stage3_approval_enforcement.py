"""Tests that Phase 2C approval gates are enforced before stage resume."""

import json
from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.pipeline.orchestrator import Orchestrator
from autoad_researcher.pipeline.patch_application_stage import run_patch_application_stage
from autoad_researcher.pipeline.patch_planning_stage import run_patch_planning_stage
from autoad_researcher.pipeline.runner_execute_stage import run_runner_execute_stage
from autoad_researcher.schemas.stage3_acceptance import Stage3AcceptanceRequest, Stage3AcceptanceStageRecord


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _intent_ok(run_dir: Path, run_id: str) -> None:
    _write_json(run_dir / "ui_chat" / "intent_draft.json", {
        "run_id": run_id,
        "source": "ui_chat",
        "research_goal": "Reduce memory.",
        "problem_type": "resource_efficiency",
        "primary_metrics": ["peak_gpu_memory_mb"],
        "guardrail_metrics": ["instance_auroc"],
        "allowed_change_scope": [],
        "forbidden_change_scope": [],
        "benchmark_scope": {},
        "success_criteria": "memory decreases",
        "risks": [],
        "open_questions": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _write_json(run_dir / "approvals" / "intent_confirmation.json", {
        "run_id": run_id,
        "checkpoint": "intent_confirmation",
        "decision": "approved",
        "reviewer": "local_user",
        "source_artifact": "ui_chat/intent_draft.json",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def _stage3_approval(run_dir: Path, filename: str, decision_type: str, confirmed: bool, run_id: str) -> None:
    _write_json(run_dir / "approvals" / filename, {
        "run_id": run_id,
        "decision_type": decision_type,
        "confirmed_by_user": confirmed,
        "user_confirmation_text": "approved",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evidence_kind": "approval_artifact",
    })


def test_patch_planner_resume_cannot_bypass_missing_intent_confirmation(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "patch_planner"
    _write_json(stage_dir / "patch_planner_approval_request.json", {"already": "exists"})

    record = run_patch_planning_stage("run_gate", run_dir, stage_dir)

    assert record.status == "blocked"
    assert record.blocked_reason == "blocked_missing_approval:intent_confirmation"


def test_patch_planner_resume_passes_with_intent_confirmation(tmp_path: Path):
    run_id = "run_gate"
    run_dir = tmp_path / run_id
    stage_dir = run_dir / "patch_planner"
    _intent_ok(run_dir, run_id)
    _write_json(stage_dir / "patch_planner_approval_request.json", {"already": "exists"})

    record = run_patch_planning_stage(run_id, run_dir, stage_dir)

    assert record.status == "passed"


def test_patch_applicator_resume_cannot_bypass_missing_patch_approval(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "patch_applicator"
    _write_json(stage_dir / "patch_runner_handoff.json", {"already": "exists"})
    _write_json(run_dir / "patch_planner" / "patch_planner_approval_request.json", {"ok": True})

    record = run_patch_application_stage("run_gate", run_dir, stage_dir)

    assert record.status == "blocked"
    assert record.blocked_reason == "blocked_missing_approval:patch_approval"


def test_patch_applicator_resume_passes_with_patch_approval(tmp_path: Path):
    run_id = "run_gate"
    run_dir = tmp_path / run_id
    stage_dir = run_dir / "patch_applicator"
    _write_json(stage_dir / "patch_runner_handoff.json", {"already": "exists"})
    _write_json(run_dir / "patch_planner" / "patch_planner_approval_request.json", {"ok": True})
    _stage3_approval(run_dir, "patch_approval.json", "patch_approval", True, run_id)

    record = run_patch_application_stage(run_id, run_dir, stage_dir)

    assert record.status == "passed"


def test_runner_execute_resume_cannot_bypass_missing_run_approval(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    stage_dir = run_dir / "runner_execute"
    _write_json(stage_dir / "experiment_execution_handoff.json", {"already": "exists"})
    _write_json(run_dir / "patch_applicator" / "patch_runner_handoff.json", {"ok": True})

    record = run_runner_execute_stage("run_gate", run_dir, stage_dir)

    assert record.status == "blocked"
    assert record.blocked_reason == "blocked_missing_approval:run_approval"


def test_runner_execute_resume_passes_with_run_approval_and_env(tmp_path: Path, monkeypatch):
    run_id = "run_gate"
    run_dir = tmp_path / run_id
    stage_dir = run_dir / "runner_execute"
    _write_json(stage_dir / "experiment_execution_handoff.json", {"already": "exists"})
    _write_json(run_dir / "patch_applicator" / "patch_runner_handoff.json", {"ok": True})
    _stage3_approval(run_dir, "run_approval.json", "run_approval", True, run_id)
    monkeypatch.setenv("AUTOAD_L3_REAL_EXECUTION_ALLOWED", "1")

    record = run_runner_execute_stage(run_id, run_dir, stage_dir)

    assert record.status == "passed"


def test_orchestrator_blocks_downstream_after_gated_stage(tmp_path: Path, monkeypatch):
    def fake_run_stage(self, stage, request, run_dir, stage_dir):
        if stage == "patch_planner":
            return Stage3AcceptanceStageRecord(
                stage="patch_planner",
                status="blocked",
                blocked_reason="blocked_missing_approval:intent_confirmation",
            )
        return Stage3AcceptanceStageRecord(
            stage=stage,
            status="passed",
            handoff_sha256="a" * 64,
            artifacts=[{"relative_path": f"{stage}/artifact.json", "sha256": "a" * 64, "artifact_type": "test"}],
        )

    monkeypatch.setattr(Orchestrator, "_run_stage", fake_run_stage)
    request = Stage3AcceptanceRequest(run_id="run_gate", runs_root=str(tmp_path), mode="l1-l2")

    records = Orchestrator()._execute_pipeline(request, tmp_path / "run_gate", tmp_path / "run_gate" / "stage3_acceptance")

    assert records[6].stage == "patch_planner"
    assert records[6].status == "blocked"
    assert records[7].status == "blocked"
    assert records[7].blocked_reason.startswith("blocked_upstream")
