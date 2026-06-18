"""Step 3.9: Resource budget assessment and comparison."""

from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.results_analysis import (
    BaselineResourceAggregate,
    BundleBudgetAssessment,
    BundleResourceAggregate,
    ResourceComparisonReport,
    ResourceDelta,
    VariantBudgetAssessment,
    VariantResourceAggregate,
)


def validate_bundle_resource_coverage(
    bundle_aggregate: BundleResourceAggregate,
    expected_reports: list[ArtifactReferenceV2],
) -> bool:
    """Check that a bundle aggregate covers all expected resource reports."""
    actual_refs = set(
        (ref.artifact_id, ref.sha256) for ref in bundle_aggregate.resource_reports
    )
    expected_refs = set(
        (ref.artifact_id, ref.sha256) for ref in expected_reports
    )
    return actual_refs == expected_refs


def determine_budget_assessment(
    variant_id: str,
    aggregate: VariantResourceAggregate,
    budget_gpu_hours: float,
) -> VariantBudgetAssessment:
    """Determine whether a variant stayed within its GPU-hour budget."""
    remaining = budget_gpu_hours - aggregate.gpu_hours
    within_budget = remaining >= 0
    if within_budget:
        reason = (
            f"variant {variant_id} used {aggregate.gpu_hours:.2f} GPU-hours "
            f"({remaining:.2f} remaining)"
        )
    else:
        reason = (
            f"variant {variant_id} exceeded budget by {-remaining:.2f} GPU-hours "
            f"(used {aggregate.gpu_hours:.2f}, budget {budget_gpu_hours:.2f})"
        )
    return VariantBudgetAssessment(
        variant_id=variant_id,
        resource_aggregate=aggregate,
        budget_remaining=max(remaining, 0.0),
        within_budget=within_budget,
        reason=reason,
    )


def compare_per_experiment_usage(
    variant_aggregates: list[VariantResourceAggregate],
    baseline_aggregate: BaselineResourceAggregate,
) -> list[ResourceDelta]:
    """Compare per-experiment resource usage against the baseline."""
    deltas: list[ResourceDelta] = []
    for variant in variant_aggregates:
        deltas.append(
            ResourceDelta(
                variant_id=variant.variant_id,
                delta_gpu_hours=variant.gpu_hours - baseline_aggregate.gpu_hours,
                delta_wall_time=variant.wall_time - baseline_aggregate.wall_time,
            )
        )
    return deltas


def derive_per_experiment_budget_reason(
    variant_id: str,
    within_budget: bool,
    used: float,
    budget: float,
) -> str:
    """Derive a human-readable reason string for a budget assessment."""
    if within_budget:
        remaining = budget - used
        return (
            f"variant {variant_id} used {used:.2f} GPU-hours "
            f"({remaining:.2f} remaining)"
        )
    overshoot = used - budget
    return (
        f"variant {variant_id} exceeded budget by {overshoot:.2f} GPU-hours "
        f"(used {used:.2f}, budget {budget:.2f})"
    )


def determine_bundle_budget_assessment(
    bundle_id: str,
    bundle_aggregate: BundleResourceAggregate,
    budget_ref: ArtifactReferenceV2,
    budget_gpu_hours: float,
) -> BundleBudgetAssessment:
    """Determine whether the bundle stayed within its overall budget."""
    within_budget = bundle_aggregate.total_gpu_hours <= budget_gpu_hours
    if within_budget:
        remaining = budget_gpu_hours - bundle_aggregate.total_gpu_hours
        reason = (
            f"bundle {bundle_id} used {bundle_aggregate.total_gpu_hours:.2f} GPU-hours "
            f"({remaining:.2f} remaining)"
        )
    else:
        overshoot = bundle_aggregate.total_gpu_hours - budget_gpu_hours
        reason = (
            f"bundle {bundle_id} exceeded budget by {overshoot:.2f} GPU-hours "
            f"(used {bundle_aggregate.total_gpu_hours:.2f}, budget {budget_gpu_hours:.2f})"
        )
    return BundleBudgetAssessment(
        bundle_id=bundle_id,
        bundle_aggregate=bundle_aggregate,
        budget_ref=budget_ref,
        within_budget=within_budget,
        reason=reason,
    )


def validate_resource_comparison_report(
    report: ResourceComparisonReport,
) -> ResourceComparisonReport:
    """Validate a resource comparison report for internal consistency."""
    variant_ids_in_aggregates = {a.variant_id for a in report.variant_aggregates}
    variant_ids_in_deltas = {d.variant_id for d in report.deltas}
    variant_ids_in_assessments = {a.variant_id for a in report.per_variant_assessments}
    all_ids = variant_ids_in_aggregates | variant_ids_in_deltas | variant_ids_in_assessments
    for vid in all_ids:
        if vid not in variant_ids_in_aggregates:
            raise ValueError(f"variant {vid} missing from variant_aggregates")
        if vid not in variant_ids_in_deltas:
            raise ValueError(f"variant {vid} missing from deltas")
        if vid not in variant_ids_in_assessments:
            raise ValueError(f"variant {vid} missing from per_variant_assessments")
    return report
