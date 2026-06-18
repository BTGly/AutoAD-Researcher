"""Step 3.9: Resource budget assessment — sealed implementation.

Matches the sealed contract in docs/3.9开发计划.md v2.12.
"""

from typing import Literal

from autoad_researcher.schemas.artifacts import ArtifactReferenceV2, ResolvedArtifact
from autoad_researcher.schemas.execution import ExecutionManifest
from autoad_researcher.schemas.experiment_planning import ResourceBudget
from autoad_researcher.schemas.results_analysis import (
    BundleBudgetAssessment,
    BundleResourceAggregate,
    ResourceComparisonReport,
    VariantBudgetAssessment,
    VariantResourceAggregate,
)


def determine_budget_assessment(
    variant_id: str,
    budget: ResourceBudget | None,
    resource_budget_ref: ArtifactReferenceV2 | None,
    usage_aggregate: VariantResourceAggregate | None,
) -> VariantBudgetAssessment:
    """Per-variant deterministic budget assessment (per-experiment cap only).

    Does NOT compare against max_total_gpu_hours — that is handled by
    BundleBudgetAssessment. Only checks max unit GPU-hours vs
    max_per_experiment_gpu_hours.
    """
    if budget is None:
        return VariantBudgetAssessment(
            variant_id=variant_id,
            status="not_assessable",
            reason="resource budget artifact not found or identity mismatch",
            resource_budget_ref=None,
            resource_usage_refs=[],
        )
    if resource_budget_ref is None:
        return VariantBudgetAssessment(
            variant_id=variant_id,
            status="not_assessable",
            reason="resource budget reference missing for resolved ResourceBudget artifact",
            resource_budget_ref=None,
            resource_usage_refs=usage_aggregate.attempt_report_refs if usage_aggregate else [],
        )
    if usage_aggregate is None or usage_aggregate.measurement_status == "not_available":
        return VariantBudgetAssessment(
            variant_id=variant_id,
            status="not_assessable",
            reason="measurement_status=not_available",
            resource_budget_ref=resource_budget_ref,
            resource_usage_refs=usage_aggregate.attempt_report_refs if usage_aggregate else [],
        )
    if not usage_aggregate.per_unit_actual_gpu_hours:
        return VariantBudgetAssessment(
            variant_id=variant_id,
            status="not_assessable",
            reason="per_unit_actual_gpu_hours is empty — no GPU-hours data available",
            resource_budget_ref=resource_budget_ref,
            resource_usage_refs=usage_aggregate.attempt_report_refs,
        )
    status = compare_per_experiment_usage(usage_aggregate, budget)
    return VariantBudgetAssessment(
        variant_id=variant_id,
        status=status,
        reason=derive_per_experiment_budget_reason(variant_id, usage_aggregate, budget, status),
        resource_budget_ref=resource_budget_ref,
        resource_usage_refs=usage_aggregate.attempt_report_refs,
    )


def compare_per_experiment_usage(
    usage_aggregate: VariantResourceAggregate,
    budget: ResourceBudget,
) -> Literal["within_budget", "near_budget", "exceeded_budget"]:
    """Compare max single-unit GPU-hours against max_per_experiment_gpu_hours.

    Rules:
      - near_budget threshold = 90% of budget cap
      - exceeded_budget = over budget cap
    """
    per_limit = budget.limits.max_per_experiment_gpu_hours
    max_unit_gpu_hours = max(usage_aggregate.per_unit_actual_gpu_hours.values(), default=0.0)

    if per_limit == 0.0:
        if max_unit_gpu_hours == 0.0:
            return "within_budget"
        return "exceeded_budget"

    per_ratio = max_unit_gpu_hours / per_limit
    if per_ratio > 1.0:
        return "exceeded_budget"
    if per_ratio >= 0.9:
        return "near_budget"
    return "within_budget"


def derive_per_experiment_budget_reason(
    variant_id: str,
    usage_aggregate: VariantResourceAggregate,
    budget: ResourceBudget,
    status: Literal["within_budget", "near_budget", "exceeded_budget"],
) -> str:
    """Generate per-variant, per-experiment budget judgment reason string."""
    max_unit_gpu_hours = max(usage_aggregate.per_unit_actual_gpu_hours.values(), default=0.0)
    return (
        f"variant_id={variant_id}; status={status}; "
        f"max_unit_gpu_hours={max_unit_gpu_hours:.6f}/"
        f"{budget.limits.max_per_experiment_gpu_hours:.6f}"
    )


def determine_bundle_budget_assessment(
    bundle: BundleResourceAggregate,
    budget: ResourceBudget,
    resource_budget_ref: ArtifactReferenceV2,
    expected_baseline_unit_ids: set[str],
    expected_variant_unit_ids: dict[str, set[str]],
) -> BundleBudgetAssessment:
    """Determine whether the bundle stayed within its total GPU-hour budget.

    Performs exact coverage validation: all expected unit/variant IDs must
    be present in the bundle aggregate, and no unexpected IDs may appear.
    """
    actual_baseline_unit_ids = set(bundle.baseline.per_unit_actual_gpu_hours.keys())
    actual_variant_unit_ids = {
        vid: set(agg.per_unit_actual_gpu_hours.keys())
        for vid, agg in bundle.per_variant.items()
    }

    missing_baseline = expected_baseline_unit_ids - actual_baseline_unit_ids
    unexpected_baseline = actual_baseline_unit_ids - expected_baseline_unit_ids

    missing_variant_ids: list[str] = []
    unexpected_variant_ids: list[str] = []
    all_expected_baseline = set(expected_baseline_unit_ids)
    all_actual_baseline = set(actual_baseline_unit_ids)
    all_expected = set(expected_variant_unit_ids.keys())
    all_actual = set(bundle.per_variant.keys())

    missing_var = all_expected - all_actual
    unexpected_var = all_actual - all_expected
    missing_variant_ids.extend(missing_var)
    unexpected_variant_ids.extend(unexpected_var)

    has_coverage_issue = bool(
        missing_baseline or unexpected_baseline or missing_variant_ids or unexpected_variant_ids
    )

    if has_coverage_issue or budget is None:
        total = bundle.total_actual_gpu_hours
        mx = bundle.max_unit_actual_gpu_hours
        has_data = bool(actual_baseline_unit_ids or actual_variant_unit_ids)
        return BundleBudgetAssessment(
            status="not_assessable",
            max_unit_actual_gpu_hours=mx if has_data else None,
            bundle_total_actual_gpu_hours=total if has_data else None,
            resource_budget_ref=resource_budget_ref if budget else None,
            resource_usage_refs=[],
            missing_unit_ids=sorted(missing_baseline),
            unexpected_unit_ids=sorted(unexpected_baseline),
            missing_variant_ids=sorted(missing_variant_ids),
            unexpected_variant_ids=sorted(unexpected_variant_ids),
            reason="coverage mismatch" if has_coverage_issue else "no budget data",
        )

    bundle_total = bundle.total_actual_gpu_hours
    max_total = budget.limits.max_total_gpu_hours

    if max_total == 0.0:
        if bundle_total == 0.0:
            status: Literal["within_budget", "near_budget", "exceeded_budget"] = "within_budget"
        else:
            status = "exceeded_budget"
    else:
        ratio = bundle_total / max_total
        if ratio > 1.0:
            status = "exceeded_budget"
        elif ratio >= 0.9:
            status = "near_budget"
        else:
            status = "within_budget"

    # Collect all usage refs from baseline + variants
    all_usage_refs = list(bundle.baseline.attempt_report_refs)
    for va in bundle.per_variant.values():
        all_usage_refs.extend(va.attempt_report_refs)

    return BundleBudgetAssessment(
        status=status,
        max_unit_actual_gpu_hours=bundle.max_unit_actual_gpu_hours,
        bundle_total_actual_gpu_hours=bundle_total,
        resource_budget_ref=resource_budget_ref,
        resource_usage_refs=all_usage_refs,
        missing_unit_ids=sorted(missing_baseline),
        unexpected_unit_ids=sorted(unexpected_baseline),
        missing_variant_ids=sorted(missing_variant_ids),
        unexpected_variant_ids=sorted(unexpected_variant_ids),
        reason=(
            f"bundle_total={bundle_total:.6f}/"
            f"{max_total:.6f}; status={status}"
        ),
    )


def validate_resource_comparison_report(
    report: ResourceComparisonReport,
    budget: ResolvedArtifact[ResourceBudget],
    execution_manifest: ExecutionManifest,
) -> None:
    """Service-level validator: re-derive bundle budget assessment and compare.

    Derives expected unit/variant ID sets from execution_manifest and
    calls determine_bundle_budget_assessment to recompute, then asserts
    the report's stored assessment matches.
    """
    if budget.verified_sha256 != budget.ref.sha256:
        raise ValueError("resource budget verified SHA mismatch")

    expected_baseline_unit_ids: set[str] = set()
    expected_variant_unit_ids: dict[str, set[str]] = {}
    for unit in execution_manifest.unit_records:
        if unit.variant_id is None:
            expected_baseline_unit_ids.add(unit.unit_id)
        else:
            expected_variant_unit_ids.setdefault(unit.variant_id, set()).add(unit.unit_id)

    if report.bundle is None:
        raise ValueError("report.bundle must not be None")

    expected = determine_bundle_budget_assessment(
        bundle=report.bundle,
        budget=budget.payload,
        resource_budget_ref=budget.ref,
        expected_baseline_unit_ids=expected_baseline_unit_ids,
        expected_variant_unit_ids=expected_variant_unit_ids,
    )

    if report.bundle_budget_assessment != expected:
        raise ValueError(
            "bundle_budget_assessment does not match re-derived value: "
            f"report={report.bundle_budget_assessment}, re-derived={expected}"
        )


def validate_bundle_resource_coverage(
    bundle_aggregate: BundleResourceAggregate,
    expected_baseline_unit_ids: set[str],
    expected_variant_unit_ids: dict[str, set[str]],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Exact-set coverage validation for bundle resource aggregate.

    Returns (missing_unit_ids, unexpected_unit_ids, missing_variant_ids, unexpected_variant_ids).
    """
    actual_baseline = set(bundle_aggregate.baseline.per_unit_actual_gpu_hours.keys())
    missing_units = sorted(expected_baseline_unit_ids - actual_baseline)
    unexpected_units = sorted(actual_baseline - expected_baseline_unit_ids)

    expected_var = set(expected_variant_unit_ids.keys())
    actual_var = set(bundle_aggregate.per_variant.keys())
    missing_variant = sorted(expected_var - actual_var)
    unexpected_variant = sorted(actual_var - expected_var)

    return missing_units, unexpected_units, missing_variant, unexpected_variant
