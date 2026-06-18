"""Tests for 3.9 analysis service functions — deltas, crossval, conclusions, budget."""

import pytest

from autoad_researcher.analysis.budget import (
    compare_per_experiment_usage,
    derive_per_experiment_budget_reason,
    determine_budget_assessment,
    determine_bundle_budget_assessment,
    validate_bundle_resource_coverage,
    validate_resource_comparison_report,
)
from autoad_researcher.analysis.conclusions import (
    derive_idea_support,
)
from autoad_researcher.analysis.crossval import (
    validate_aggregate_from_observations,
)
from autoad_researcher.analysis.delta import (
    compute_deltas,
    compute_resource_deltas,
)
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.results_analysis import (
    AggregatedMetricComparison,
    AggregatedMetricKey,
    BaselineResourceAggregate,
    VariantBudgetAssessment,
    VariantResourceAggregate,
    BundleResourceAggregate,
    CurrentRunBaselineMetricRef,
    EvidenceSufficiency,
    PairedMetricObservation,
    ResourceComparisonReport,
    ResourceDelta,
    ResolvedMetricEvidence,
    ResolvedValidityEvidence,
    VariantResourceAggregate,
)

_SHA = "a" * 64


def _ref(artifact_id="art"):
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type="report",
        locator=f"runs/run_test/{artifact_id}.json",
        sha256=_SHA,
    )


# ── compute_deltas ────────────────────────────────────────────────────


class TestComputeDeltas:
    def test_both_values_present(self):
        obs = PairedMetricObservation(
            variant_metric_name="var_auroc",
            variant_value=0.95,
            variant_parse_status="parsed",
            variant_artifact_ref=_ref("var"),
            baseline_metric_name="base_auroc",
            baseline_value=0.90,
            baseline_parse_status="parsed",
            baseline_artifact_ref=_ref("base"),
        )
        result = compute_deltas([obs])
        assert len(result) == 1
        assert result[0].raw_delta == pytest.approx(0.05)
        assert result[0].relative_delta_pct == pytest.approx(5.555555, rel=0.001)

    def test_variant_none(self):
        obs = PairedMetricObservation(
            variant_metric_name="var",
            variant_value=None,
            variant_parse_status="missing",
            variant_artifact_ref=_ref("var"),
            baseline_metric_name="base",
            baseline_value=0.90,
            baseline_parse_status="parsed",
            baseline_artifact_ref=_ref("base"),
        )
        result = compute_deltas([obs])
        assert result[0].raw_delta is None

    def test_baseline_none(self):
        obs = PairedMetricObservation(
            variant_metric_name="var",
            variant_value=0.95,
            variant_parse_status="parsed",
            variant_artifact_ref=_ref("var"),
            baseline_metric_name="base",
            baseline_value=None,
            baseline_parse_status="missing",
            baseline_artifact_ref=_ref("base"),
        )
        result = compute_deltas([obs])
        assert result[0].raw_delta is None

    def test_baseline_zero(self):
        obs = PairedMetricObservation(
            variant_metric_name="var",
            variant_value=0.0,
            variant_parse_status="parsed",
            variant_artifact_ref=_ref("var"),
            baseline_metric_name="base",
            baseline_value=0.0,
            baseline_parse_status="parsed",
            baseline_artifact_ref=_ref("base"),
        )
        result = compute_deltas([obs])
        assert result[0].raw_delta == pytest.approx(0.0)
        assert result[0].relative_delta_pct is None

    def test_empty_list(self):
        result = compute_deltas([])
        assert result == []


# ── compute_resource_deltas ───────────────────────────────────────────


class TestComputeResourceDeltas:
    def test_positive_delta(self):
        variant = VariantResourceAggregate(
            variant_id="v1", total_attempts=3, gpu_hours=6.0, wall_time=10800,
        )
        baseline = BaselineResourceAggregate(
            total_attempts=2, gpu_hours=4.0, wall_time=7200,
        )
        delta = compute_resource_deltas(variant, baseline)
        assert delta.delta_gpu_hours == 2.0
        assert delta.delta_wall_time == 3600.0


# ── validate_aggregate_from_observations ──────────────────────────────


class TestValidateAggregateFromObservations:
    def test_single_observation(self):
        key = AggregatedMetricKey(metric_name="auroc", dataset_row="bottle", unit="ratio")
        obs = PairedMetricObservation(
            variant_metric_name="var_auroc",
            variant_value=0.95,
            variant_parse_status="parsed",
            variant_artifact_ref=_ref("var"),
            baseline_metric_name="base_auroc",
            baseline_value=0.90,
            baseline_parse_status="parsed",
            baseline_artifact_ref=_ref("base"),
        )
        comp = validate_aggregate_from_observations(key, [obs])
        assert comp.mean_variant == pytest.approx(0.95)
        assert comp.mean_baseline == pytest.approx(0.90)
        assert comp.mean_delta == pytest.approx(0.05)

    def test_multiple_observations(self):
        key = AggregatedMetricKey(metric_name="auroc", dataset_row="bottle", unit="ratio")
        obs1 = PairedMetricObservation(
            variant_metric_name="var", variant_value=0.95,
            variant_parse_status="parsed", variant_artifact_ref=_ref("v1"),
            baseline_metric_name="base", baseline_value=0.90,
            baseline_parse_status="parsed", baseline_artifact_ref=_ref("b1"),
        )
        obs2 = PairedMetricObservation(
            variant_metric_name="var", variant_value=0.93,
            variant_parse_status="parsed", variant_artifact_ref=_ref("v2"),
            baseline_metric_name="base", baseline_value=0.91,
            baseline_parse_status="parsed", baseline_artifact_ref=_ref("b2"),
        )
        comp = validate_aggregate_from_observations(key, [obs1, obs2])
        assert comp.mean_variant == pytest.approx(0.94)
        assert comp.mean_baseline == pytest.approx(0.905)

    def test_no_observations(self):
        key = AggregatedMetricKey(metric_name="auroc", dataset_row="bottle", unit="ratio")
        comp = validate_aggregate_from_observations(key, [])
        assert comp.mean_variant is None
        assert comp.mean_baseline is None
        assert comp.mean_delta is None


# ── derive_idea_support ───────────────────────────────────────────────


class TestDeriveIdeaSupport:
    def test_supported(self):
        key = AggregatedMetricKey(metric_name="auroc", dataset_row="bottle", unit="ratio")
        comp = AggregatedMetricComparison(
            key=key,
            mean_delta=0.05,
            mean_variant=0.95,
            mean_baseline=0.90,
            mean_relative_delta_pct=5.56,
        )
        evidence = ResolvedMetricEvidence(metric_comparisons=[comp])
        conclusion = derive_idea_support(
            variant_id="v1",
            metric_evidence=evidence,
            validity_evidence=ResolvedValidityEvidence(overall_valid=True),
            sufficiency=EvidenceSufficiency(sufficiency_summary="sufficient"),
        )
        assert conclusion.conclusion == "supported"

    def test_not_supported(self):
        key = AggregatedMetricKey(metric_name="auroc", dataset_row="bottle", unit="ratio")
        comp = AggregatedMetricComparison(
            key=key,
            mean_delta=-0.05,
            mean_variant=0.85,
            mean_baseline=0.90,
            mean_relative_delta_pct=-5.56,
        )
        evidence = ResolvedMetricEvidence(metric_comparisons=[comp])
        conclusion = derive_idea_support(
            variant_id="v1",
            metric_evidence=evidence,
            validity_evidence=ResolvedValidityEvidence(overall_valid=True),
            sufficiency=EvidenceSufficiency(sufficiency_summary="sufficient"),
        )
        assert conclusion.conclusion == "not_supported"

    def test_no_evidence(self):
        conclusion = derive_idea_support(
            variant_id="v1",
            metric_evidence=None,
            validity_evidence=None,
            sufficiency=None,
        )
        assert conclusion.conclusion == "inconclusive"
        assert conclusion.confidence == 0.0

    def test_no_comparisons(self):
        evidence = ResolvedMetricEvidence(metric_comparisons=[])
        conclusion = derive_idea_support(
            variant_id="v1",
            metric_evidence=evidence,
            validity_evidence=ResolvedValidityEvidence(overall_valid=True),
            sufficiency=None,
        )
        assert conclusion.conclusion == "inconclusive"

    def test_invalid_validity(self):
        key = AggregatedMetricKey(metric_name="auroc", dataset_row="bottle", unit="ratio")
        comp = AggregatedMetricComparison(key=key, mean_delta=0.05)
        evidence = ResolvedMetricEvidence(metric_comparisons=[comp])
        conclusion = derive_idea_support(
            variant_id="v1",
            metric_evidence=evidence,
            validity_evidence=ResolvedValidityEvidence(overall_valid=False),
            sufficiency=None,
        )
        assert conclusion.conclusion == "inconclusive"


# ── determine_budget_assessment ───────────────────────────────────────


class TestDetermineBudgetAssessment:
    def test_within_budget(self):
        agg = VariantResourceAggregate(
            variant_id="v1", total_attempts=2, gpu_hours=4.0, wall_time=7200,
        )
        assessment = determine_budget_assessment("v1", agg, 10.0)
        assert assessment.within_budget is True
        assert assessment.budget_remaining == 6.0

    def test_exceeded_budget(self):
        agg = VariantResourceAggregate(
            variant_id="v1", total_attempts=5, gpu_hours=15.0, wall_time=27000,
        )
        assessment = determine_budget_assessment("v1", agg, 10.0)
        assert assessment.within_budget is False
        assert assessment.budget_remaining == 0.0

    def test_exact_budget(self):
        agg = VariantResourceAggregate(
            variant_id="v1", total_attempts=2, gpu_hours=10.0, wall_time=18000,
        )
        assessment = determine_budget_assessment("v1", agg, 10.0)
        assert assessment.within_budget is True
        assert assessment.budget_remaining == 0.0


# ── compare_per_experiment_usage ──────────────────────────────────────


class TestComparePerExperimentUsage:
    def test_multiple_variants(self):
        variants = [
            VariantResourceAggregate(
                variant_id="v1", total_attempts=2, gpu_hours=4.0, wall_time=7200,
            ),
            VariantResourceAggregate(
                variant_id="v2", total_attempts=3, gpu_hours=6.0, wall_time=10800,
            ),
        ]
        baseline = BaselineResourceAggregate(
            total_attempts=1, gpu_hours=2.0, wall_time=3600,
        )
        deltas = compare_per_experiment_usage(variants, baseline)
        assert len(deltas) == 2
        assert deltas[0].delta_gpu_hours == 2.0
        assert deltas[1].delta_gpu_hours == 4.0


# ── derive_per_experiment_budget_reason ────────────────────────────────


class TestDerivePerExperimentBudgetReason:
    def test_within_budget(self):
        reason = derive_per_experiment_budget_reason("v1", True, 4.0, 10.0)
        assert "6.00 remaining" in reason

    def test_exceeded(self):
        reason = derive_per_experiment_budget_reason("v1", False, 15.0, 10.0)
        assert "exceeded" in reason
        assert "5.00" in reason


# ── determine_bundle_budget_assessment ────────────────────────────────


class TestDetermineBundleBudgetAssessment:
    def test_within_budget(self):
        agg = BundleResourceAggregate(
            bundle_id="b1", total_gpu_hours=8.0, total_wall_time=28800,
        )
        assessment = determine_bundle_budget_assessment("b1", agg, _ref("budget"), 10.0)
        assert assessment.within_budget is True

    def test_exceeded(self):
        agg = BundleResourceAggregate(
            bundle_id="b1", total_gpu_hours=15.0, total_wall_time=54000,
        )
        assessment = determine_bundle_budget_assessment("b1", agg, _ref("budget"), 10.0)
        assert assessment.within_budget is False


# ── validate_bundle_resource_coverage ─────────────────────────────────


class TestValidateBundleResourceCoverage:
    def test_full_coverage(self):
        agg = BundleResourceAggregate(
            bundle_id="b1",
            resource_reports=[_ref("r1"), _ref("r2")],
            total_gpu_hours=5.0,
            total_wall_time=18000,
        )
        expected = [_ref("r1"), _ref("r2")]
        assert validate_bundle_resource_coverage(agg, expected) is True

    def test_missing_report(self):
        agg = BundleResourceAggregate(
            bundle_id="b1",
            resource_reports=[_ref("r1")],
            total_gpu_hours=2.0,
            total_wall_time=7200,
        )
        expected = [_ref("r1"), _ref("r2")]  # r2 is missing
        assert validate_bundle_resource_coverage(agg, expected) is False


# ── validate_resource_comparison_report ────────────────────────────────


class TestValidateResourceComparisonReport:
    def test_consistent(self):
        agg = VariantResourceAggregate(
            variant_id="v1", total_attempts=2, gpu_hours=4.0, wall_time=7200,
        )
        delta = ResourceDelta(variant_id="v1", delta_gpu_hours=2.0, delta_wall_time=3600)
        ba = VariantBudgetAssessment(
            variant_id="v1",
            resource_aggregate=agg,
            budget_remaining=6.0,
            within_budget=True,
            reason="test",
        )
        report = ResourceComparisonReport(
            variant_aggregates=[agg],
            deltas=[delta],
            per_variant_assessments=[ba],
        )
        validated = validate_resource_comparison_report(report)
        assert validated is report

    def test_missing_variant_in_aggregates(self):
        agg = VariantResourceAggregate(
            variant_id="v1", total_attempts=2, gpu_hours=4.0, wall_time=7200,
        )
        delta_v1 = ResourceDelta(variant_id="v1", delta_gpu_hours=2.0, delta_wall_time=3600)
        delta_v2 = ResourceDelta(variant_id="v2", delta_gpu_hours=1.0, delta_wall_time=1800)
        ba = VariantBudgetAssessment(
            variant_id="v1",
            resource_aggregate=agg,
            budget_remaining=6.0,
            within_budget=True,
            reason="test",
        )
        report = ResourceComparisonReport(
            variant_aggregates=[agg],
            deltas=[delta_v1, delta_v2],
            per_variant_assessments=[ba],
        )
        with pytest.raises(ValueError, match="v2 missing from variant_aggregates"):
            validate_resource_comparison_report(report)

    def test_missing_variant_in_deltas(self):
        agg = VariantResourceAggregate(
            variant_id="v1", total_attempts=2, gpu_hours=4.0, wall_time=7200,
        )
        ba = VariantBudgetAssessment(
            variant_id="v1",
            resource_aggregate=agg,
            budget_remaining=6.0,
            within_budget=True,
            reason="test",
        )
        report = ResourceComparisonReport(
            variant_aggregates=[agg],
            deltas=[],
            per_variant_assessments=[ba],
        )
        with pytest.raises(ValueError, match="v1 missing from deltas"):
            validate_resource_comparison_report(report)
