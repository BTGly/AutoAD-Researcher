"""Tests for source-neutral resolution, budget, and guard builders."""

import pytest

from autoad_researcher.experiment.builders import (
    ResolutionPlanBuildError,
    build_guard_policy,
    build_resolution_plans,
    build_resource_budget,
)
from autoad_researcher.schemas.experiment_planning import (
    EntryResourceEstimate,
    ExperimentMatrix,
    ExperimentPlanningInput,
    ExperimentResolvableDimension,
    ExperimentVariantInput,
    MatrixEntry,
    ResourceLimits,
    ThresholdCriterion,
)


def _matrix() -> ExperimentMatrix:
    return ExperimentMatrix(
        matrix_id="matrix",
        schema_version=1,
        protocol_fingerprint="fp",
        seeds=[42],
        variants=["var_A"],
        entries=[
            MatrixEntry(entry_id="baseline_s42", variant_id=None, stage="baseline", seed=42, intent_ref="baseline", depends_on=[], shared_axes=["protocol"], independent_axes=["seed_42"]),
            MatrixEntry(entry_id="var_A_smoke", variant_id="var_A", stage="smoke", seed=42, intent_ref="smoke", depends_on=[], shared_axes=["protocol"], independent_axes=["smoke"]),
            MatrixEntry(entry_id="var_A_full_s42", variant_id="var_A", stage="full", seed=42, intent_ref="full", depends_on=["var_A_smoke"], shared_axes=["protocol"], independent_axes=["seed_42"]),
        ],
    )


def _estimate(entry_id: str, value: float) -> EntryResourceEstimate:
    return EntryResourceEstimate(entry_id=entry_id, estimated_gpu_hours_low=value, estimated_gpu_hours_high=value, planning_value=value, estimate_source="test", confidence="high")


def test_resolution_plan_requires_real_matrix_target_and_compiles_criterion():
    input_ = ExperimentPlanningInput(
        run_id="run_builder",
        variants=[ExperimentVariantInput(variant_id="var_A", variant_label="Variant A", idea_id="idea_001", primary_hook_id="hook_01")],
        resolvable_dimensions=[ExperimentResolvableDimension(
            variant_id="var_A", dimension="resource", verification_stage="smoke",
            observable="gpu_hours", observation_source="runner", acceptance_criterion=ThresholdCriterion(criterion_type="value_below_threshold", metric_name="gpu_hours", threshold=1.0),
        )],
    )

    plans = build_resolution_plans(input_, _matrix(), "fp")

    assert plans.resolutions[0].target_entry_ids == ["var_A_smoke"]


def test_resolution_plan_rejects_missing_variant_stage():
    input_ = ExperimentPlanningInput(
        run_id="run_builder",
        variants=[ExperimentVariantInput(variant_id="var_A", variant_label="Variant A", idea_id="idea_001", primary_hook_id="hook_01")],
        resolvable_dimensions=[ExperimentResolvableDimension(
            variant_id="var_A", dimension="resource", verification_stage="fit",
            observable="gpu_hours", observation_source="runner", acceptance_criterion=ThresholdCriterion(criterion_type="value_below_threshold", metric_name="gpu_hours", threshold=1.0),
        )],
    )

    with pytest.raises(ResolutionPlanBuildError, match="no matching matrix entry"):
        build_resolution_plans(input_, _matrix(), "fp")


def test_budget_and_guard_cover_every_matrix_entry():
    matrix = _matrix()
    budget = build_resource_budget(
        matrix, "fp", 1,
        ResourceLimits(max_total_gpu_hours=10, max_per_experiment_gpu_hours=2, available_gpu_count=1, available_gpu_type="A100"),
        [_estimate("baseline_s42", 1), _estimate("var_A_smoke", 0.5), _estimate("var_A_full_s42", 1.5)],
    )

    guard = build_guard_policy(matrix, "fp", budget)
    assert budget.total_estimate.total_gpu_hours == 3
    assert next(item for item in guard.guards if item.guard_id == "g_resource_cap").parameters["max_gpu_hours"] == 10
