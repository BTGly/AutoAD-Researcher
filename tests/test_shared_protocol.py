"""Tests for shared_protocol.py — Step 1 builder."""

import pytest
from pydantic import ValidationError

from autoad_researcher.experiment.shared_protocol import build_shared_protocol
from autoad_researcher.schemas.experiment_planning import (
    BaselineExecutionPolicy,
    BaselineResultRef,
    PlanningInputRefs,
    SeedMetric,
    Stage35Input,
    SupplementalEvaluationRefs,
)
from autoad_researcher.schemas.transfer_design import (
    DerivedClaim,
    IdeaContract,
    IdeaTransferAnalysis,
    UserProvidedIdeaContract,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_planning_refs():
    return PlanningInputRefs(
        repository_fingerprint="repo_fp",
        environment_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        asset_manifest_sha256="c" * 64,
    )


def _mock_supplemental():
    return SupplementalEvaluationRefs()


def _mock_artifact_refv2(artifact_id="x", sha256="a" * 64):
    from autoad_researcher.schemas.artifacts import ArtifactReferenceV2

    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type="report",
        locator="runs/r1/x.json",
        sha256=sha256,
    )


def _mock_seed_metric(seed=42, value=0.85):
    return SeedMetric(seed=seed, metric_name="image_auroc", metric_value=value)


def _mock_baseline_result_ref(seeds=None, metric_name="image_auroc"):
    if seeds is None:
        seeds = [42]
    metrics = [SeedMetric(seed=s, metric_name=metric_name, metric_value=0.85) for s in seeds]
    return BaselineResultRef(
        source_run_id="run_baseline_001",
        repository_fingerprint="repo_fp",
        baseline_config_sha256="g" * 64,
        dataset_manifest_sha256="b" * 64,
        evaluation_contract_sha256="c" * 64,
        environment_lock_sha256="d" * 64,
        asset_manifest_sha256="e" * 64,
        command_sha256="f" * 64,
        seeds=seeds,
        per_seed_metrics=metrics,
        result_artifact_refs=[_mock_artifact_refv2()],
        validity_report_ref=_mock_artifact_refv2("vrpt"),
        validity_status="valid",
        completed_seed_ids=seeds,
    )


def _mock_idea():
    return IdeaContract(
        idea_id="idea_001",
        idea_source=UserProvidedIdeaContract(
            user_description="Test idea",
            mechanism_hypothesis=DerivedClaim(value="test"),
            transfer_relevance=DerivedClaim(value="relevant"),
        ),
        must_preserve_behaviors=[],
        confirmation_status="pending",
    )


def _mock_transfer_analysis():
    return IdeaTransferAnalysis(
        idea_id="idea_001",
        variant_analyses={},
    )


def _mock_stage35_input():
    return Stage35Input(
        run_id="run_001",
        confirmed_idea=_mock_idea(),
        transfer_analysis=_mock_transfer_analysis(),
        transfer_constraints=[],
        variants=[],
        nonblocking_warnings=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_build_protocol_run_fresh():
    protocol = build_shared_protocol(
        stage35_input=_mock_stage35_input(),
        planning_input_refs=_mock_planning_refs(),
        supplemental_refs=_mock_supplemental(),
        evaluation_protocol_ref=_mock_artifact_refv2("eval_proto"),
        baseline_method="patchcore",
        baseline_config_sha256="g" * 64,
        seeds=[42, 43],
        primary_metric="image_auroc",
        metric_direction="maximize",
        protected_paths=[],
        must_not_change=[],
        protocol_evidence_ids=[],
    )
    assert protocol.baseline_policy.mode == "run_fresh"
    assert protocol.seeds == [42, 43]
    assert protocol.primary_metric == "image_auroc"
    assert len(protocol.protocol_fingerprint) == 64
    assert protocol.protocol_fingerprint != ""


def test_build_protocol_reuse_existing():
    seeds = [42, 43]
    reuse_source = _mock_baseline_result_ref(seeds, metric_name="auroc")
    policy = BaselineExecutionPolicy(
        mode="reuse_existing",
        reuse_source=reuse_source,
        seeds=seeds,
    )
    protocol = build_shared_protocol(
        stage35_input=_mock_stage35_input(),
        planning_input_refs=_mock_planning_refs(),
        supplemental_refs=_mock_supplemental(),
        evaluation_protocol_ref=_mock_artifact_refv2("eval_proto"),
        baseline_method="patchcore",
        baseline_config_sha256="g" * 64,
        seeds=seeds,
        primary_metric="auroc",
        metric_direction="maximize",
        protected_paths=[],
        must_not_change=[],
        protocol_evidence_ids=[],
        baseline_policy=policy,
    )
    assert protocol.baseline_policy.mode == "reuse_existing"
    assert protocol.baseline_policy.reuse_source is not None


def test_build_protocol_duplicate_seeds_rejected():
    with pytest.raises(ValidationError, match="unique"):
        build_shared_protocol(
            stage35_input=_mock_stage35_input(),
            planning_input_refs=_mock_planning_refs(),
            supplemental_refs=_mock_supplemental(),
            evaluation_protocol_ref=_mock_artifact_refv2("eval_proto"),
            baseline_method="patchcore",
            baseline_config_sha256="g" * 64,
            seeds=[42, 42],
            primary_metric="auroc",
            metric_direction="maximize",
            protected_paths=[],
            must_not_change=[],
            protocol_evidence_ids=[],
        )


def test_build_protocol_mismatched_baseline_policy_seeds():
    seeds = [42]
    reuse_source = _mock_baseline_result_ref(seeds, metric_name="auroc")
    policy = BaselineExecutionPolicy(
        mode="reuse_existing",
        reuse_source=reuse_source,
        seeds=[99],
    )
    with pytest.raises(ValidationError, match="baseline_policy"):
        build_shared_protocol(
            stage35_input=_mock_stage35_input(),
            planning_input_refs=_mock_planning_refs(),
            supplemental_refs=_mock_supplemental(),
            evaluation_protocol_ref=_mock_artifact_refv2("eval_proto"),
            baseline_method="patchcore",
            baseline_config_sha256="g" * 64,
            seeds=seeds,
            primary_metric="auroc",
            metric_direction="maximize",
            protected_paths=[],
            must_not_change=[],
            protocol_evidence_ids=[],
            baseline_policy=policy,
        )


def test_build_protocol_fingerprint_deterministic():
    kwargs = dict(
        stage35_input=_mock_stage35_input(),
        planning_input_refs=_mock_planning_refs(),
        supplemental_refs=_mock_supplemental(),
        evaluation_protocol_ref=_mock_artifact_refv2("eval_proto"),
        baseline_method="patchcore",
        baseline_config_sha256="g" * 64,
        seeds=[42],
        primary_metric="auroc",
        metric_direction="maximize",
        protected_paths=[],
        must_not_change=[],
        protocol_evidence_ids=[],
        protocol_id="fixed_id",
    )
    p1 = build_shared_protocol(**kwargs)
    p2 = build_shared_protocol(**kwargs)
    assert p1.protocol_fingerprint == p2.protocol_fingerprint


def test_build_protocol_seeds_empty_rejected():
    with pytest.raises(ValidationError):
        build_shared_protocol(
            stage35_input=_mock_stage35_input(),
            planning_input_refs=_mock_planning_refs(),
            supplemental_refs=_mock_supplemental(),
            evaluation_protocol_ref=_mock_artifact_refv2("eval_proto"),
            baseline_method="patchcore",
            baseline_config_sha256="g" * 64,
            seeds=[],
            primary_metric="auroc",
            metric_direction="maximize",
            protected_paths=[],
            must_not_change=[],
            protocol_evidence_ids=[],
        )
