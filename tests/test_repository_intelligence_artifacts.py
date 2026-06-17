"""Tests for Repository Intelligence R8 artifact synthesis."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.repository_intelligence import (
    AnalysisObservation,
    AnalysisProgress,
    ArtifactClaim,
    EvaluationContractDraftArtifact,
    ModifiablePathsArtifact,
    synthesize_repository_artifacts,
)


def progress() -> AnalysisProgress:
    return AnalysisProgress(
        schema_version=1,
        iteration=1,
        stage_status="synthesis_ready",
        coverage={
            "repository_summary": "confirmed",
            "entrypoints": "confirmed",
            "dependencies": "confirmed",
            "configurations": "confirmed",
            "evaluation": "checked_unknown",
            "data_assets": "checked_unknown",
        },
        evidence_count=3,
        tool_calls_used=3,
        file_reads_used=2,
        search_calls_used=1,
        llm_calls_used=0,
        input_tokens_used=0,
        new_evidence_count_last_cycle=3,
        no_progress_cycles=0,
        budget_exhausted=False,
        unresolved_blockers=[],
        next_actions=[],
    )


def observations() -> list[AnalysisObservation]:
    return [
        AnalysisObservation(
            observation_id="obs_read_001",
            category="repository_summary",
            summary="Read repository file README.md",
            status="confirmed",
            evidence_ids=["ev_readme"],
            created_at="2026-06-17T00:00:00Z",
        ),
        AnalysisObservation(
            observation_id="obs_read_002",
            category="dependencies",
            summary="Read repository file pyproject.toml",
            status="confirmed",
            evidence_ids=["ev_pyproject"],
            created_at="2026-06-17T00:00:00Z",
        ),
        AnalysisObservation(
            observation_id="obs_search_001",
            category="entrypoints",
            summary="Found repository text matches for pattern train",
            status="candidate",
            evidence_ids=["ev_train"],
            created_at="2026-06-17T00:00:00Z",
        ),
    ]


def test_confirmed_artifact_claim_requires_evidence():
    with pytest.raises(ValidationError, match="confirmed artifact claim requires evidence_ids"):
        ArtifactClaim(
            claim_id="claim_001",
            status="confirmed",
            confidence="high",
            summary="unsupported",
            evidence_ids=[],
        )


def test_synthesis_writes_seven_formal_artifacts_and_sha(tmp_path: Path):
    result = synthesize_repository_artifacts(
        output_dir=tmp_path,
        observations=observations(),
        progress=progress(),
    )

    assert result.paths.path_set() == set(result.artifact_sha256)
    assert len(result.artifact_sha256) == 7
    for relative_path, sha in result.artifact_sha256.items():
        assert (tmp_path / relative_path).is_file()
        assert len(sha) == 64


def test_evaluation_is_draft_and_path_policy_is_proposal(tmp_path: Path):
    synthesize_repository_artifacts(output_dir=tmp_path, observations=observations(), progress=progress())

    evaluation = EvaluationContractDraftArtifact.model_validate_json((tmp_path / "evaluation_contract_draft.json").read_text())
    path_policy = ModifiablePathsArtifact.model_validate_json((tmp_path / "modifiable_paths.json").read_text())

    assert evaluation.status == "draft"
    assert path_policy.policy_status == "proposal"


def test_environment_context_makes_no_final_decision(tmp_path: Path):
    synthesize_repository_artifacts(output_dir=tmp_path, observations=observations(), progress=progress())

    payload = json.loads((tmp_path / "environment_context.json").read_text(encoding="utf-8"))

    assert payload["final_decision"] is False
    assert payload["dependency_files"][0]["evidence_ids"] == ["ev_pyproject"]


def test_unknowns_are_expressed_separately(tmp_path: Path):
    synthesize_repository_artifacts(output_dir=tmp_path, observations=observations(), progress=progress())

    payload = json.loads((tmp_path / "uncertainties.json").read_text(encoding="utf-8"))
    categories = [group["category"] for group in payload["groups"]]

    assert "blocking_entrypoint_selection" in categories
    assert "blocking_evaluation_contract" in categories
