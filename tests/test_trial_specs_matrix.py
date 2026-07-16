"""Experiment trials compile directly from source-neutral planning input."""

from autoad_researcher.experiment.matrix_builder import build_matrix
from autoad_researcher.experiment.trial_specs import build_trial_specs
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.experiment_planning import (
    BaselineExecutionPolicy,
    ExperimentPlanningInput,
    ExperimentVariantInput,
    PlanningInputRefs,
    PreparationPhase,
    SharedExperimentProtocol,
    SupplementalEvaluationRefs,
)


def _input(phase: PreparationPhase = PreparationPhase.NONE) -> ExperimentPlanningInput:
    return ExperimentPlanningInput(
        run_id="run_001",
        variants=[
            ExperimentVariantInput(
                variant_id="var_A",
                variant_label="Variant A",
                idea_id="idea_001",
                primary_hook_id="hook_01",
                preparation_phase=phase,
                risk_level="low",
            )
        ],
    )


def _protocol() -> SharedExperimentProtocol:
    return SharedExperimentProtocol(
        protocol_id="proto_test",
        schema_version=1,
        planning_input_refs=PlanningInputRefs(
            repository_fingerprint="rf",
            environment_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64,
            asset_manifest_sha256="c" * 64,
        ),
        supplemental_refs=SupplementalEvaluationRefs(),
        evaluation_protocol_ref=ArtifactReferenceV2(
            artifact_id="eval", artifact_type="config", locator="x", sha256="a" * 64,
        ),
        baseline_method="baseline",
        baseline_config_sha256="g" * 64,
        baseline_policy=BaselineExecutionPolicy(mode="run_fresh", seeds=[42, 43]),
        seeds=[42, 43],
        primary_metric="auroc",
        metric_direction="maximize",
        protected_paths=[],
        must_not_change=[],
        protocol_evidence_ids=[],
        protocol_fingerprint="fp_test",
    )


def test_no_training_variant_has_smoke_and_full_without_fit():
    specs = build_trial_specs(_input(), "fp_test")

    variant = specs.variants[0]
    assert variant.preparation_phase == PreparationPhase.NONE
    assert variant.fit is None
    assert variant.smoke is not None
    assert variant.full is not None


def test_fit_variant_binds_shared_fit_output_to_each_evaluation_seed():
    specs = build_trial_specs(_input(PreparationPhase.FIT), "fp_test")
    matrix = build_matrix(_protocol(), specs)

    assert len([entry for entry in matrix.entries if entry.stage == "fit"]) == 1
    assert len([entry for entry in matrix.entries if entry.stage == "full"]) == 2
    assert len(matrix.input_bindings) == 3  # one smoke + two full evaluations
