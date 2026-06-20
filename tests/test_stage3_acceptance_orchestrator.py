"""Tests for Step 3.10 acceptance orchestration."""

import json
from pathlib import Path

import yaml

from autoad_researcher.pipeline.orchestrator import Orchestrator
from autoad_researcher.schemas.stage3_acceptance import Stage3AcceptanceRequest


def _write_input_task(run_dir: Path) -> Path:
    path = run_dir / "input_task.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({
        "run_id": run_dir.name,
        "request": "Reimplement PatchCore on MVTec AD bottle.",
        "source_ids": ["patchcore_paper", "patchcore_repo"],
    }), encoding="utf-8")
    return path


def test_l1_l2_blocks_at_paper_intelligence_when_pdf_missing(tmp_path):
    """Pipeline runs intake, then blocks at paper_intelligence because PDF is outside tmp_path."""
    run_dir = tmp_path / "run_310"
    _write_input_task(run_dir)

    request = Stage3AcceptanceRequest(run_id="run_310", runs_root=str(tmp_path), mode="l1-l2")
    result = Orchestrator().run(request)

    assert result.status == "blocked"
    assert result.failed_stage == "paper_intelligence"
    assert result.failure_reason == "blocked_missing_artifact:paper_intelligence"
    artifact_dir = tmp_path / "run_310" / "stage3_acceptance"
    for name in (
        "stage3_acceptance_manifest.json",
        "end_to_end_run_report.json",
        "artifact_chain_validation.json",
        "security_gate_report.json",
        "release_candidate_report.md",
    ):
        assert (artifact_dir / name).exists()

    manifest = json.loads((artifact_dir / "stage3_acceptance_manifest.json").read_text(encoding="utf-8"))
    assert manifest["all_stages_completed"] is False
    assert len(manifest["stages"]) == 11
    assert manifest["failed_stage"] == "paper_intelligence"


def test_missing_input_task_blocks_intake(tmp_path):
    request = Stage3AcceptanceRequest(
        run_id="run_missing",
        runs_root=str(tmp_path),
        mode="l1-l2",
    )

    result = Orchestrator().run(request)

    assert result.status == "blocked"
    assert result.failed_stage == "intake"
    assert result.failure_reason == "blocked_missing_artifact:intake"
    report_path = tmp_path / "run_missing" / "stage3_acceptance" / "end_to_end_run_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"


def test_l3_preflight_is_blocked_without_real_execution(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    request = Stage3AcceptanceRequest(run_id="run_l3", runs_root=str(tmp_path), mode="l3-preflight")

    result = Orchestrator().run(request)

    assert result.status == "blocked"
    assert "blocked_l3_preflight_missing" in result.failure_reason
    artifact_dir = tmp_path / "run_l3" / "stage3_acceptance"
    assert (artifact_dir / "end_to_end_run_report.json").exists()
    assert not (artifact_dir / "stages").exists()
