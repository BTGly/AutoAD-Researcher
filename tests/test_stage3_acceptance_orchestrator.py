"""Tests for Step 3.10 acceptance orchestration."""

import json

from autoad_researcher.pipeline.orchestrator import Orchestrator
from autoad_researcher.schemas.stage3_acceptance import (
    ArtifactChainBinding,
    Stage3AcceptanceRequest,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def test_l1_l2_happy_path_generates_acceptance_artifacts(tmp_path):
    request = Stage3AcceptanceRequest(run_id="run_310", runs_root=str(tmp_path), mode="l1-l2")

    result = Orchestrator().run(request)

    assert result.status == "passed"
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
    assert manifest["all_stages_completed"] is True
    assert len(manifest["stages"]) == 11


def test_missing_required_artifact_blocks_acceptance(tmp_path):
    request = Stage3AcceptanceRequest(
        run_id="run_missing",
        runs_root=str(tmp_path),
        mode="l1-l2",
        required_artifact_paths={"intake": ["input_task.yaml"]},
    )

    result = Orchestrator().run(request)

    assert result.status == "blocked"
    assert result.failed_stage == "intake"
    assert result.failure_reason == "blocked_missing_artifact:intake"
    report_path = tmp_path / "run_missing" / "stage3_acceptance" / "end_to_end_run_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"


def test_sha_chain_mismatch_fails_acceptance(tmp_path):
    request = Stage3AcceptanceRequest(
        run_id="run_mismatch",
        runs_root=str(tmp_path),
        mode="l1-l2",
        expected_chain_bindings=[
            ArtifactChainBinding(
                upstream_stage="intake",
                downstream_stage="repository_intelligence",
                upstream_handoff_sha256=SHA_A,
                downstream_input_ref_sha256=SHA_B,
                match=False,
            )
        ],
    )

    result = Orchestrator().run(request)

    assert result.status == "failed"
    assert result.failure_reason == "failed_sha_chain_mismatch"
    chain_path = tmp_path / "run_mismatch" / "stage3_acceptance" / "artifact_chain_validation.json"
    chain = json.loads(chain_path.read_text(encoding="utf-8"))
    assert chain["all_match"] is False


def test_l3_preflight_is_blocked_without_real_execution(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    request = Stage3AcceptanceRequest(run_id="run_l3", runs_root=str(tmp_path), mode="l3-preflight")

    result = Orchestrator().run(request)

    assert result.status == "blocked"
    assert "blocked_l3_preflight_missing" in result.failure_reason
    artifact_dir = tmp_path / "run_l3" / "stage3_acceptance"
    assert (artifact_dir / "end_to_end_run_report.json").exists()
    assert not (artifact_dir / "stages").exists()
