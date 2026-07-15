"""Tests for Phase 2D HITL gate status UI helpers."""

import json
from pathlib import Path

from autoad_researcher.ui.artifact_viewer import (
    BLOCKED_REASON_HINTS,
    RECOMMENDED_FILES,
    get_approval_gate_report,
)
from autoad_researcher.ui.research_chat import build_hitl_gate_status_rows


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_approval_gate_report_helper_reads_known_stage(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    _write_json(
        run_dir / "patch_applicator" / "approval_gate_report.json",
        {
            "schema_version": 1,
            "run_id": "run_gate",
            "stage": "patch_applicator",
            "gate_name": "patch_approval",
            "status": "blocked",
            "required_artifact": "approvals/patch_approval.json",
            "blocked_reason": "blocked_missing_approval:patch_approval",
            "checked_at": "2026-07-03T00:00:00+00:00",
        },
    )

    report = get_approval_gate_report(run_dir, "patch_applicator")

    assert report is not None
    assert report["gate_name"] == "patch_approval"
    assert get_approval_gate_report(run_dir, "unknown") is None


def test_recommended_files_include_approval_gate_reports():
    for stage in ("patch_applicator", "runner_execute"):
        assert "approval_gate_report.json" in RECOMMENDED_FILES[stage]
    assert "approval_gate_report.json" not in RECOMMENDED_FILES["patch_planner"]


def test_blocked_reason_hints_cover_expected_gate_failures():
    assert "patch plan" in BLOCKED_REASON_HINTS["blocked_missing_approval:patch_approval"]
    assert "AUTOAD_L3_REAL_EXECUTION_ALLOWED=1" in BLOCKED_REASON_HINTS[
        "blocked_real_execution_not_allowed:run_approval"
    ]


def test_hitl_gate_status_rows_use_reports_and_hints(tmp_path: Path):
    run_dir = tmp_path / "run_gate"
    _write_json(
        run_dir / "patch_applicator" / "approval_gate_report.json",
        {
            "schema_version": 1,
            "run_id": "run_gate",
            "stage": "patch_applicator",
            "gate_name": "patch_approval",
            "status": "blocked",
            "required_artifact": "approvals/patch_approval.json",
            "blocked_reason": "blocked_missing_approval:patch_approval",
            "checked_at": "2026-07-03T00:00:00+00:00",
        },
    )

    rows = build_hitl_gate_status_rows(run_dir)

    assert rows[0]["stage"] == "patch_applicator"
    assert rows[0]["status"] == "blocked"
    assert rows[0]["next_action"] == BLOCKED_REASON_HINTS["blocked_missing_approval:patch_approval"]
    assert rows[1]["status"] == "not_checked"
