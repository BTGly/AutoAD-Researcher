from __future__ import annotations

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
    CLARIFICATION_INPUT_JSON,
    INTENT_DRAFT_DIR,
    ResearchIntentDraft,
    save_clarification_input,
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


def _prepare_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run_ui_bridge"
    draft = _draft(run_id=run_dir.name)
    save_intent_draft(run_dir, draft)
    save_clarification_input(run_dir, draft)
    return run_dir


def test_missing_clarification_blocks_bridge(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="missing clarification_input.json"):
        build_input_task_from_clarification(tmp_path / "run_ui_bridge")


def test_clarification_writes_input_task_without_summary_confirmation(tmp_path: Path):
    run_dir = _prepare_run(tmp_path)

    output = save_input_task_yaml_from_clarification(run_dir)

    data = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert data["run_id"] == "run_ui_bridge"
    assert data["baseline"] == "PatchCore"
    assert data["dataset"] == "MVTec AD bottle"


def test_existing_input_task_requires_overwrite(tmp_path: Path):
    run_dir = _prepare_run(tmp_path)
    save_input_task_yaml_from_clarification(run_dir)
    with pytest.raises(FileExistsError, match="already exists"):
        save_input_task_yaml_from_clarification(run_dir)
    assert save_input_task_yaml_from_clarification(run_dir, overwrite=True).is_file()


def test_source_report_hashes_only_clarification(tmp_path: Path):
    run_dir = _prepare_run(tmp_path)
    save_input_task_yaml_from_clarification(run_dir)

    report = json.loads(
        (run_dir / INTENT_DRAFT_DIR / INPUT_TASK_SOURCE_REPORT_JSON).read_text(encoding="utf-8")
    )

    assert report["source"] == f"{INTENT_DRAFT_DIR}/{CLARIFICATION_INPUT_JSON}"
    assert len(report["source_sha256"]) == 64
    assert set(report) == {
        "schema_version",
        "run_id",
        "source",
        "created_output",
        "source_sha256",
        "created_at",
    }


def test_secret_like_payload_is_rejected(tmp_path: Path):
    run_dir = _prepare_run(tmp_path)
    path = run_dir / INTENT_DRAFT_DIR / CLARIFICATION_INPUT_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    data["input_task"]["request"] = "contains sk-secret12345"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="secret-like content forbidden"):
        build_input_task_from_clarification(run_dir)


def test_status_reports_readiness_from_clarification(tmp_path: Path):
    status = get_intake_bridge_status(_prepare_run(tmp_path))

    assert status["can_generate"] is True
    assert status["input_task_exists"] is False
    assert status["clarification_exists"] is True
