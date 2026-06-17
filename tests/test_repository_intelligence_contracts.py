"""Tests for Repository Intelligence R1 contracts."""

import pytest
from pydantic import TypeAdapter, ValidationError

from autoad_researcher.repository_intelligence import (
    AnalysisControlSignal,
    EvidenceIndexRecord,
    EvidenceRef,
    RepositoryAgentBudget,
    RepositoryArtifactPaths,
    RepositoryCandidate,
    RepositoryClaim,
    RepositoryIdentityEvidenceRef,
    RepositoryIntelligenceRequest,
    RepositoryIntelligenceResult,
    RepositoryResolution,
    RepositorySource,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
COMMIT = "a" * 40


def budget() -> RepositoryAgentBudget:
    return RepositoryAgentBudget(
        max_total_tool_calls=100,
        max_total_llm_calls=20,
        max_total_input_tokens=100_000,
        max_total_output_tokens=20_000,
        max_discovery_search_calls=5,
        max_discovery_fetch_calls=5,
        max_analysis_tool_calls=60,
        max_analysis_file_reads=40,
        max_analysis_search_calls=20,
        max_analysis_llm_calls=10,
        max_repair_tool_calls=10,
        max_repair_llm_calls=3,
        max_repairs=2,
    )


def artifact_paths() -> RepositoryArtifactPaths:
    return RepositoryArtifactPaths(
        repository_summary="repository_summary.json",
        entrypoints="entrypoints.json",
        dependency_evidence="dependency_evidence.json",
        modifiable_paths="modifiable_paths.json",
        evaluation_contract_draft="evaluation_contract_draft.json",
        environment_context="environment_context.json",
        uncertainties="uncertainties.json",
    )


def artifact_sha256(paths: RepositoryArtifactPaths) -> dict[str, str]:
    return {path: SHA_A for path in paths.path_set()}


def test_request_custom_budget_required():
    with pytest.raises(ValidationError, match="custom budget_profile requires budget"):
        RepositoryIntelligenceRequest(
            schema_version=1,
            request_id="req_001",
            run_id="run_demo",
            user_goal="analyze repository",
            discovery_allowed=True,
            user_confirmation_policy="when_ambiguous",
            budget_profile="custom",
        )


def test_budget_separates_analysis_and_repair_reserve():
    b = budget()

    assert b.max_analysis_tool_calls == 60
    assert b.max_repair_tool_calls == 10
    assert b.max_no_progress_cycles == 2


def test_candidate_contains_default_branch_and_resolved_commit():
    c = RepositoryCandidate(
        candidate_id="cand_001",
        canonical_url="https://github.com/example/repo",
        owner="example",
        repository="repo",
        default_branch="main",
        requested_ref=None,
        resolved_commit=COMMIT,
        official_link_found=True,
        author_or_org_match=True,
        paper_reference_found=False,
        method_name_match="strong",
        is_fork=False,
        is_archived=False,
        confidence="high",
        selection_rationale="official repository link",
        evidence_ids=["ev_001"],
        warnings=[],
    )

    assert c.default_branch == "main"
    assert c.resolved_commit == COMMIT


def test_candidate_rejects_bad_commit_sha():
    with pytest.raises(ValidationError):
        RepositoryCandidate(
            candidate_id="cand_001",
            canonical_url="https://github.com/example/repo",
            owner="example",
            repository="repo",
            default_branch="main",
            requested_ref=None,
            resolved_commit="not-a-commit",
            official_link_found=True,
            author_or_org_match=True,
            paper_reference_found=False,
            method_name_match="strong",
            is_fork=False,
            is_archived=False,
            confidence="high",
            selection_rationale="official repository link",
        )


def test_resolution_requires_selected_candidate_and_commit_when_resolved():
    with pytest.raises(ValidationError, match="resolved status requires selected_candidate_id"):
        RepositoryResolution(
            schema_version=1,
            status="resolved",
            selected_candidate_id=None,
            alternative_candidate_ids=[],
            resolved_ref="main",
            resolved_commit=COMMIT,
            resolution_reason="single candidate",
            user_confirmation_required=False,
        )


def test_repository_source_rejects_absolute_local_path_label():
    with pytest.raises(ValidationError, match="absolute path forbidden"):
        RepositorySource(
            schema_version=1,
            source_id="source_001",
            kind="github_public",
            canonical_remote_url="https://github.com/example/repo",
            requested_ref="main",
            acquisition_profile="shallow_ref",
            resolved_commit=COMMIT,
            tree_sha=SHA_A,
            detached_head=True,
            dirty=False,
            local_path_label="/tmp/repo",
            source_fingerprint=SHA_B,
        )


def test_evidence_ref_discriminated_union_accepts_identity_record():
    record = EvidenceIndexRecord(
        schema_version=1,
        evidence=RepositoryIdentityEvidenceRef(
            source_kind="repository_identity",
            evidence_id="ev_identity",
            source_id="source_001",
            canonical_remote_url="https://github.com/example/repo",
            resolved_commit=COMMIT,
            tree_sha=SHA_A,
            detached_head=True,
            dirty=False,
            attestation_sha256=SHA_B,
            tool_call_ids=["tool_001"],
            trust_level="repository_identity",
        ),
    )

    assert record.evidence.source_kind == "repository_identity"


def test_evidence_ref_rejects_bad_file_path_and_sha():
    adapter = TypeAdapter(EvidenceRef)

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "source_kind": "repository_file",
                "evidence_id": "ev_file",
                "source_id": "source_001",
                "repository_commit": COMMIT,
                "path": "../escape.py",
                "file_sha256": SHA_A,
                "start_line": 1,
                "end_line": 2,
                "snippet_sha256": "not-sha",
                "tool_call_id": "tool_001",
                "trust_level": "code_fact",
            }
        )


def test_analysis_control_signal_validates_coverage_literals():
    signal = AnalysisControlSignal(
        decision="continue_reading",
        coverage={"entrypoints": "not_checked", "dependencies": "confirmed"},
        new_evidence_count=2,
        unresolved_blockers=[],
        next_actions=["read pyproject.toml"],
    )

    assert signal.coverage["dependencies"] == "confirmed"

    with pytest.raises(ValidationError):
        AnalysisControlSignal(
            decision="continue_reading",
            coverage={"entrypoints": "maybe"},
            new_evidence_count=0,
        )


def test_confirmed_claim_requires_evidence():
    with pytest.raises(ValidationError, match="confirmed claim requires evidence_ids"):
        RepositoryClaim(
            claim_id="claim_001",
            subject="repository",
            predicate="has_training_entrypoint",
            value=True,
            status="confirmed",
            confidence="high",
            evidence_ids=[],
        )


def test_result_requires_sha_for_each_formal_artifact():
    paths = artifact_paths()
    shas = artifact_sha256(paths)
    shas.pop("uncertainties.json")

    with pytest.raises(ValidationError, match="artifact_sha256 missing"):
        RepositoryIntelligenceResult(
            schema_version=1,
            request_id="req_001",
            run_id="run_demo",
            status="partial_success",
            source_id="source_001",
            artifacts=paths,
            artifact_sha256=shas,
            evidence_index_path="evidence_index.jsonl",
            evidence_index_sha256=SHA_B,
            validation_report_path="evidence_validation.json",
            validation_report_sha256=SHA_C,
        )


def test_result_rejects_non_formal_artifact_sha_path():
    paths = artifact_paths()
    shas = artifact_sha256(paths)
    shas["extra.json"] = SHA_A

    with pytest.raises(ValidationError, match="non-formal"):
        RepositoryIntelligenceResult(
            schema_version=1,
            request_id="req_001",
            run_id="run_demo",
            status="partial_success",
            source_id="source_001",
            artifacts=paths,
            artifact_sha256=shas,
            evidence_index_path="evidence_index.jsonl",
            evidence_index_sha256=SHA_B,
            validation_report_path="evidence_validation.json",
            validation_report_sha256=SHA_C,
        )
