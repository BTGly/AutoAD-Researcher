"""Tests for Phase 2D intent-to-intake bridge."""

import json
from pathlib import Path

import pytest
import yaml

from autoad_researcher.ui.intake_bridge import (
    INPUT_TASK_SOURCE_REPORT_JSON,
    build_input_task_from_clarification,
    get_intake_bridge_status,
    save_input_task_yaml_from_clarification,
)
from autoad_researcher.ui.intent_draft import (
    APPROVALS_DIR,
    CLARIFICATION_INPUT_JSON,
    INTENT_CONFIRMATION_JSON,
    INTENT_DRAFT_DIR,
    ResearchIntentDraft,
    save_clarification_input,
    save_intent_confirmation,
    save_intent_draft,
)


def _draft(run_id: str = "run_ui_bridge") -> ResearchIntentDraft:
    return ResearchIntentDraft(
        run_id=run_id,
        research_goal="Reduce PatchCore runtime while keeping AUROC stable.",
        problem_type="resource_efficiency",
        primary_metrics=["wall_time_seconds"],
        guardrail_metrics=["instance_auroc"],
        allowed_change_scope=["patchcore/sampler.py"],
        forbidden_change_scope=["configs/"],
        benchmark_scope={"dataset": "MVTec AD bottle", "baseline": "PatchCore"},
        success_criteria="Runtime improves without meaningful AUROC regression.",
        risks=["AUROC regression"],
        open_questions=[],
    )


def _prepare_run(tmp_path: Path, *, decision: str = "approved") -> Path:
    run_dir = tmp_path / "run_ui_bridge"
    draft = _draft(run_id=run_dir.name)
    save_intent_draft(run_dir, draft)
    save_clarification_input(run_dir, draft)
    save_intent_confirmation(run_dir, decision=decision)
    return run_dir


def test_missing_clarification_blocks_bridge(tmp_path: Path):
    run_dir = tmp_path / "run_ui_bridge"

    with pytest.raises(FileNotFoundError, match="missing clarification_input.json"):
        build_input_task_from_clarification(run_dir)


def test_missing_confirmation_blocks_bridge(tmp_path: Path):
    run_dir = tmp_path / "run_ui_bridge"
    draft = _draft(run_id=run_dir.name)
    save_intent_draft(run_dir, draft)
    save_clarification_input(run_dir, draft)

    with pytest.raises(FileNotFoundError, match="missing intent_confirmation.json"):
        build_input_task_from_clarification(run_dir)


def test_rejected_confirmation_blocks_bridge(tmp_path: Path):
    run_dir = _prepare_run(tmp_path, decision="rejected")

    with pytest.raises(ValueError, match="intent_confirmation must be approved"):
        build_input_task_from_clarification(run_dir)


def test_approved_confirmation_writes_input_task_yaml(tmp_path: Path):
    run_dir = _prepare_run(tmp_path)

    output = save_input_task_yaml_from_clarification(run_dir)

    assert output == run_dir / "input_task.yaml"
    data = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert data["run_id"] == "run_ui_bridge"
    assert data["request"].startswith("Reduce PatchCore runtime")
    assert data["baseline"] == "PatchCore"
    assert data["dataset"] == "MVTec AD bottle"


def test_existing_input_task_requires_overwrite(tmp_path: Path):
    run_dir = _prepare_run(tmp_path)
    save_input_task_yaml_from_clarification(run_dir)

    with pytest.raises(FileExistsError, match="already exists"):
        save_input_task_yaml_from_clarification(run_dir)

    assert save_input_task_yaml_from_clarification(run_dir, overwrite=True).is_file()


def test_source_report_is_written_with_hashes(tmp_path: Path):
    run_dir = _prepare_run(tmp_path)

    save_input_task_yaml_from_clarification(run_dir)

    report_path = run_dir / INTENT_DRAFT_DIR / INPUT_TASK_SOURCE_REPORT_JSON
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["run_id"] == "run_ui_bridge"
    assert report["source"] == f"{INTENT_DRAFT_DIR}/{CLARIFICATION_INPUT_JSON}"
    assert report["intent_confirmation"] == f"{APPROVALS_DIR}/{INTENT_CONFIRMATION_JSON}"
    assert report["intent_confirmation_decision"] == "approved"
    assert len(report["source_sha256"]) == 64
    assert len(report["confirmation_sha256"]) == 64


def test_secret_like_payload_is_rejected(tmp_path: Path):
    run_dir = _prepare_run(tmp_path)
    path = run_dir / INTENT_DRAFT_DIR / CLARIFICATION_INPUT_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    data["input_task"]["request"] = "contains sk-secret12345"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="secret-like content forbidden"):
        build_input_task_from_clarification(run_dir)


def test_status_reports_generation_readiness(tmp_path: Path):
    run_dir = _prepare_run(tmp_path)

    status = get_intake_bridge_status(run_dir)

    assert status["can_generate"] is True
    assert status["input_task_exists"] is False
    assert status["intent_confirmation_decision"] == "approved"
