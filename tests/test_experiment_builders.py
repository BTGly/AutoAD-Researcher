"""Tests for Step 3.5 resolution/budget/guard builders."""

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
    MatrixEntry,
    ResourceLimits,
    Stage35Input,
    Stage35VariantInput,
)
from autoad_researcher.schemas.transfer_design import (
    CompatibilityDimension,
    CompatibilityStatus,
    DerivedClaim,
    IdeaContract,
    IdeaTransferAnalysis,
    ImplementationVariant,
    ResolutionClass,
    TransferStatus,
    UnresolvedDimension,
    UserProvidedIdeaContract,
    VariantRiskReport,
    VariantTransferAnalysis,
)


def test_build_resource_budget_summarizes_matrix_entries():
    matrix = _matrix()
    limits = ResourceLimits(
        max_total_gpu_hours=10.0,
        max_per_experiment_gpu_hours=2.0,
        available_gpu_count=1,
        available_gpu_type="A100",
    )

    budget = build_resource_budget(
        matrix=matrix,
        protocol_fingerprint="fp",
        protocol_version=1,
        limits=limits,
        entry_estimates=[
            _estimate("baseline_s42", 1.0),
            _estimate("var_A_smoke", 0.5),
            _estimate("var_A_full_s42", 1.5),
        ],
    )

    assert budget.budget_decision.status == "within_budget"
    assert budget.total_estimate.total_gpu_hours == 3.0
    assert budget.total_estimate.max_single_experiment_gpu_hours == 1.5
    assert set(budget.per_variant) == {"baseline", "var_A"}


def test_build_guard_policy_uses_resource_budget_limit():
    matrix = _matrix()
    limits = ResourceLimits(
        max_total_gpu_hours=7.0,
        max_per_experiment_gpu_hours=2.0,
        available_gpu_count=1,
        available_gpu_type="A100",
    )
    budget = build_resource_budget(
        matrix=matrix,
        protocol_fingerprint="fp",
        protocol_version=1,
        limits=limits,
        entry_estimates=[
            _estimate("baseline_s42", 1.0),
            _estimate("var_A_smoke", 0.5),
            _estimate("var_A_full_s42", 1.5),
        ],
    )

    policy = build_guard_policy(matrix, "fp", resource_budget=budget)

    resource_guard = next(g for g in policy.guards if g.guard_id == "g_resource_cap")
    assert resource_guard.parameters["max_gpu_hours"] == 7.0


def test_build_resolution_plans_rejects_free_text_criterion():
    with pytest.raises(ResolutionPlanBuildError, match="Structured resolution criteria"):
        build_resolution_plans(
            _stage35_input_with_unresolved_dimension(),
            _matrix(),
            "fp",
        )


def _matrix() -> ExperimentMatrix:
    return ExperimentMatrix(
        matrix_id="matrix_builder_test",
        schema_version=1,
        protocol_fingerprint="fp",
        seeds=[42],
        variants=["var_A"],
        entries=[
            MatrixEntry(
                entry_id="baseline_s42",
                variant_id=None,
                stage="baseline",
                seed=42,
                intent_ref="baseline",
                depends_on=[],
                shared_axes=["protocol"],
                independent_axes=["seed_42"],
            ),
            MatrixEntry(
                entry_id="var_A_smoke",
                variant_id="var_A",
                stage="smoke",
                seed=42,
                intent_ref="smoke",
                depends_on=[],
                shared_axes=["protocol"],
                independent_axes=["smoke"],
            ),
            MatrixEntry(
                entry_id="var_A_full_s42",
                variant_id="var_A",
                stage="full",
                seed=42,
                intent_ref="full",
                depends_on=["var_A_smoke"],
                shared_axes=["protocol"],
                independent_axes=["seed_42"],
            ),
        ],
    )


def _estimate(entry_id: str, planning_value: float) -> EntryResourceEstimate:
    return EntryResourceEstimate(
        entry_id=entry_id,
        estimated_gpu_hours_low=planning_value,
        estimated_gpu_hours_high=planning_value,
        planning_value=planning_value,
        estimate_source="unit_test",
        confidence="high",
    )


def _stage35_input_with_unresolved_dimension() -> Stage35Input:
    variant = ImplementationVariant(
        variant_id="var_A",
        variant_label="Variant A",
        idea_id="idea_001",
        primary_hook_id="hook_01",
        hook_bindings=[],
        interface_deltas=[],
        regime_changes=[],
        state_changes=[],
        adapter_required=False,
        new_dependencies=[],
        expected_behavior_rationale="Better",
        risk_level="low",
        fallback_behavior="Fallback",
    )
    return Stage35Input(
        run_id="run_builder",
        confirmed_idea=IdeaContract(
            idea_id="idea_001",
            idea_source=UserProvidedIdeaContract(
                user_description="Idea",
                mechanism_hypothesis=DerivedClaim(value="test"),
                transfer_relevance=DerivedClaim(value="relevant"),
            ),
            confirmation_status="pending",
        ),
        transfer_analysis=IdeaTransferAnalysis(idea_id="idea_001"),
        transfer_constraints=[],
        variants=[
            Stage35VariantInput(
                variant=variant,
                transfer_analysis=VariantTransferAnalysis(
                    variant_id="var_A",
                    overall_status=TransferStatus.VIABLE,
                ),
                risk_report=VariantRiskReport(
                    variant_id="var_A",
                    computed_risk_level="low",
                ),
                experiment_resolvable=[
                    UnresolvedDimension(
                        variant_id="var_A",
                        dimension=CompatibilityDimension.RESOURCE,
                        status=CompatibilityStatus.INSUFFICIENT_EVIDENCE,
                        classification=ResolutionClass.EXPERIMENT_RESOLVABLE,
                        resolution_reason="Need empirical resource check",
                        verification_target="gpu_hours",
                        acceptance_criterion="below user budget",
                        classified_by_rule_id="rule_resource",
                    )
                ],
            )
        ],
        nonblocking_warnings=[],
    )
