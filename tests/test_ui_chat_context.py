"""Tests for chat_context.py."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from autoad_researcher.ui.chat_context import build_chat_context


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _make_fake_run(tmp: Path) -> Path:
    run_dir = tmp / "runs" / "run_test_001"
    runner_dir = run_dir / "runner_execute"
    final_dir = run_dir / "final_report"
    results_dir = run_dir / "results_analysis"

    _write_json(runner_dir / "execution_manifest.json", {
        "completed_unit_count": 3, "failed_unit_count": 0,
        "blocked_unit_count": 0, "total_unit_count": 3,
    })
    _write_json(runner_dir / "runner_intake_report.json", {"status": "passed"})
    _write_json(runner_dir / "gpu_execution_evidence.json", {
        "gpu_used": True, "device_name": "TestGPU", "source": "test",
    })
    _write_json(final_dir / "final_report_facts.json", {
        "noop_patch": False, "execution_mode": "gpu_verified",
        "l3_gpu_claim": "completed", "scientific_claim": "mixed_or_inconclusive",
        "pipeline_stages": {"patch_planner": "passed", "patch_applicator": "passed",
                            "runner_execute": "passed", "results_analysis": "passed",
                            "final_report": "passed"},
    })
    (final_dir / "final_report.md").write_text("A" * 6000, encoding="utf-8")
    _write_json(results_dir / "results_analysis_handoff.json",
                {"handoff_sha256": "abc123"})
    (run_dir / "events.jsonl").write_text("event1\nevent2\n", encoding="utf-8")
    return run_dir


class TestBuildChatContext:
    def test_returns_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_fake_run(Path(tmp))
            ctx = build_chat_context(run_dir)
            assert ctx["run_id"] == "run_test_001"

    def test_available_stages_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_fake_run(Path(tmp))
            ctx = build_chat_context(run_dir)
            assert "runner_execute" in ctx["available_stages"]
            assert "final_report" in ctx["available_stages"]

    def test_missing_files_become_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "runs" / "empty_run"
            empty.mkdir(parents=True)
            ctx = build_chat_context(empty)
            assert ctx["execution_manifest"] is None
            assert ctx["gpu_evidence"] is None
            assert ctx["final_facts"] is None
            assert ctx["final_report_excerpt"] is None

    def test_report_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_fake_run(Path(tmp))
            ctx = build_chat_context(run_dir)
            excerpt = ctx["final_report_excerpt"]
            assert excerpt is not None
            assert len(excerpt) <= 5200
            assert "截断" in excerpt

    def test_events_tail_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_fake_run(Path(tmp))
            ctx = build_chat_context(run_dir)
            assert ctx["events_tail"] is not None
            assert len(ctx["events_tail"]) <= 20

    def test_no_api_key_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _make_fake_run(Path(tmp))
            ctx = build_chat_context(run_dir)
            ctx_str = json.dumps(ctx, default=str)
            assert "sk-" not in ctx_str
            assert "_api_key" not in ctx_str.lower()
