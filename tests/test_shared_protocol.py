"""Shared protocol construction is independent of a transfer pipeline."""

import pytest
from pydantic import ValidationError

from autoad_researcher.experiment.shared_protocol import build_shared_protocol
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.experiment_planning import (
    BaselineExecutionPolicy,
    BaselineResultRef,
    PlanningInputRefs,
    SeedMetric,
    SupplementalEvaluationRefs,
)


def _refs() -> PlanningInputRefs:
    return PlanningInputRefs(repository_fingerprint="repo_fp", environment_sha256="a" * 64, dataset_manifest_sha256="b" * 64, asset_manifest_sha256="c" * 64)


def _artifact() -> ArtifactReferenceV2:
    return ArtifactReferenceV2(artifact_id="eval", artifact_type="config", locator="runs/r/eval.json", sha256="d" * 64)


def _build(**kwargs):
    return build_shared_protocol(
        planning_input_refs=_refs(), supplemental_refs=SupplementalEvaluationRefs(),
        evaluation_protocol_ref=_artifact(), baseline_method="baseline", baseline_config_sha256="g" * 64,
        seeds=[42, 43], primary_metric="auroc", metric_direction="maximize",
        protected_paths=[], must_not_change=[], protocol_evidence_ids=[], **kwargs,
    )


def test_build_protocol_generates_a_stable_identity_without_transfer_input():
    protocol = _build()
    assert protocol.baseline_policy.mode == "run_fresh"
    assert len(protocol.protocol_fingerprint) == 64


def test_reused_baseline_must_match_the_planned_identity():
    reference = BaselineResultRef(
        source_run_id="run_base", repository_fingerprint="wrong_repo", baseline_config_sha256="g" * 64,
        dataset_manifest_sha256="b" * 64, evaluation_contract_sha256="d" * 64,
        environment_lock_sha256="a" * 64, asset_manifest_sha256="c" * 64, command_sha256="e" * 64,
        seeds=[42, 43], per_seed_metrics=[SeedMetric(seed=42, metric_name="auroc", metric_value=.8), SeedMetric(seed=43, metric_name="auroc", metric_value=.8)],
        result_artifact_refs=[_artifact()], validity_report_ref=_artifact(), validity_status="valid", completed_seed_ids=[42, 43],
    )
    policy = BaselineExecutionPolicy(mode="reuse_existing", reuse_source=reference, seeds=[42, 43])

    with pytest.raises(ValidationError, match="repository_fingerprint"):
        _build(baseline_policy=policy)
