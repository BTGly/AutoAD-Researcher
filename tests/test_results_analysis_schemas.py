"""Tests for 3.9 results analysis schemas — model creation, validators, constraints."""

import pytest

from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.results_analysis import (
    AggregatedMetricComparison,
    AggregatedMetricKey,
    BaselineResourceAggregate,
    BundleBudgetAssessment,
    BundleResourceAggregate,
    CurrentRunBaselineMetricRef,
    EvidenceSufficiency,
    FailureAnalysis,
    MetricObservationKey,
    NextRunProposal,
    PairedMetricObservation,
    Reflection,
    ReplicationGroup,
    ReplicationPairEvidence,
    ReportFacts,
    ReproducibilityInterpretation,
    ResolvedMetricEvidence,
    ResolvedValidityEvidence,
    ResourceComparisonReport,
    ResourceDelta,
    ReusedBaselineMetricRef,
    ValidityInterpretation,
    VariantBudgetAssessment,
    VariantResourceAggregate,
    VariantScientificConclusion,
)

_SHA = "a" * 64


def _ref(artifact_id="art"):
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type="report",
        locator=f"runs/run_test/{artifact_id}.json",
        sha256=_SHA,
    )


# ── Baseline Metric Refs ──────────────────────────────────────────────


class TestCurrentRunBaselineMetricRef:
    def test_valid(self):
        ref = CurrentRunBaselineMetricRef(
            metric_name="auroc",
            baseline_metric_artifact_ref=_ref("base_metric"),
            current_metric_name_in_run="variant_auroc",
            current_artifact_ref=_ref("variant_metric"),
            aggregation_method="mean",
        )
        assert ref.metric_name == "auroc"


class TestReusedBaselineMetricRef:
    def test_valid(self):
        ref = ReusedBaselineMetricRef(
            metric_name="auroc",
            source_run_id="run_001",
            source_artifact_ref=_ref("base_metric"),
        )
        assert ref.source_run_id == "run_001"


# ── PairedMetricObservation ───────────────────────────────────────────


class TestPairedMetricObservation:
    def test_valid(self):
        obs = PairedMetricObservation(
            variant_metric_name="variant_auroc",
            variant_value=0.95,
            variant_parse_status="parsed",
            variant_artifact_ref=_ref("var"),
            baseline_metric_name="baseline_auroc",
            baseline_value=0.90,
            baseline_parse_status="parsed",
            baseline_artifact_ref=_ref("base"),
            raw_delta=0.05,
        )
        assert obs.raw_delta == 0.05

    def test_delta_recomputed_validator(self):
        with pytest.raises(Exception, match="raw_delta"):
            PairedMetricObservation(
                variant_metric_name="var",
                variant_value=0.95,
                variant_parse_status="parsed",
                variant_artifact_ref=_ref("var"),
                baseline_metric_name="base",
                baseline_value=0.90,
                baseline_parse_status="parsed",
                baseline_artifact_ref=_ref("base"),
                raw_delta=0.10,
            )

    def test_delta_matches(self):
        obs = PairedMetricObservation(
            variant_metric_name="var",
            variant_value=0.95,
            variant_parse_status="parsed",
            variant_artifact_ref=_ref("var"),
            baseline_metric_name="base",
            baseline_value=0.90,
            baseline_parse_status="parsed",
            baseline_artifact_ref=_ref("base"),
            raw_delta=0.05,
        )
        assert obs.variant_value == 0.95

    def test_none_delta_skips_validation(self):
        obs = PairedMetricObservation(
            variant_metric_name="var",
            variant_value=0.95,
            variant_parse_status="parsed",
            variant_artifact_ref=_ref("var"),
            baseline_metric_name="base",
            baseline_value=0.90,
            baseline_parse_status="parsed",
            baseline_artifact_ref=_ref("base"),
            raw_delta=None,
        )
        assert obs.raw_delta is None


# ── Metric Keys ───────────────────────────────────────────────────────


class TestMetricObservationKey:
    def test_valid(self):
        k = MetricObservationKey(unit_id="u1", attempt_number=1, role="variant")
        assert k.unit_id == "u1"


class TestAggregatedMetricKey:
    def test_valid(self):
        k = AggregatedMetricKey(metric_name="auroc", dataset_row="bottle", unit="ratio")
        assert k.metric_name == "auroc"


# ── AggregatedMetricComparison ────────────────────────────────────────


class TestAggregatedMetricComparison:
    def test_minimal(self):
        comp = AggregatedMetricComparison(
            key=AggregatedMetricKey(
                metric_name="auroc", dataset_row="bottle", unit="ratio"
            ),
        )
        assert comp.key.metric_name == "auroc"
        assert comp.observations == []


# ── Resolved Evidence ─────────────────────────────────────────────────


class TestResolvedMetricEvidence:
    def test_valid(self):
        ev = ResolvedMetricEvidence()
        assert ev.metric_comparisons == []


class TestResolvedValidityEvidence:
    def test_defaults(self):
        ev = ResolvedValidityEvidence()
        assert ev.overall_valid is False


# ── Evidence Sufficiency ──────────────────────────────────────────────


class TestEvidenceSufficiency:
    def test_valid(self):
        es = EvidenceSufficiency(sufficiency_summary="incomplete")
        assert es.sufficiency_summary == "incomplete"
        assert es.all_metrics_accounted_for is False


# ── VariantScientificConclusion ───────────────────────────────────────


class TestVariantScientificConclusion:
    def test_supported(self):
        c = VariantScientificConclusion(
            variant_id="v1",
            conclusion="supported",
            confidence=0.9,
        )
        assert c.conclusion == "supported"

    def test_inconclusive(self):
        c = VariantScientificConclusion(
            variant_id="v1",
            conclusion="inconclusive",
            confidence=0.0,
        )
        assert c.confidence == 0.0

    def test_confidence_range(self):
        with pytest.raises(Exception):
            VariantScientificConclusion(
                variant_id="v1", conclusion="supported", confidence=1.5,
            )

    def test_with_evidence(self):
        c = VariantScientificConclusion(
            variant_id="v1",
            conclusion="supported",
            confidence=0.8,
            evidence=ResolvedMetricEvidence(
                metric_comparisons=[
                    AggregatedMetricComparison(
                        key=AggregatedMetricKey(
                            metric_name="auroc", dataset_row="bottle", unit="ratio"
                        ),
                    )
                ]
            ),
            sufficiency=EvidenceSufficiency(sufficiency_summary="sufficient"),
            supporting_metrics=["auroc"],
        )
        assert "auroc" in c.supporting_metrics


# ── Reproducibility ───────────────────────────────────────────────────


class TestReplicationPairEvidence:
    def test_valid(self):
        pair = ReplicationPairEvidence(
            pair_id="p1",
            variant_attempt_ref=_ref("var"),
            baseline_attempt_ref=_ref("base"),
            status="reproducible",
        )
        assert pair.status == "reproducible"


class TestReplicationGroup:
    def test_valid(self):
        g = ReplicationGroup(
            group_id="g1",
            variant_id="v1",
            overall_status="reproducible",
        )
        assert g.overall_status == "reproducible"


class TestReproducibilityInterpretation:
    def test_valid(self):
        r = ReproducibilityInterpretation(
            groups=[
                ReplicationGroup(
                    group_id="g1", variant_id="v1", overall_status="reproducible",
                )
            ],
            overall_reproducible=True,
        )
        assert r.overall_reproducible is True


# ── Validity ──────────────────────────────────────────────────────────


class TestValidityInterpretation:
    def test_valid(self):
        v = ValidityInterpretation(
            variant_validity_reports=[_ref("v_val")],
            baseline_validity_reports=[_ref("b_val")],
            overall_valid=True,
        )
        assert v.overall_valid is True


# ── Resources / Budget ────────────────────────────────────────────────


class TestVariantResourceAggregate:
    def test_valid(self):
        a = VariantResourceAggregate(
            variant_id="v1", total_attempts=3, gpu_hours=6.0, wall_time=10800.0,
        )
        assert a.total_actual_gpu_hours == 6.0

    def test_gpu_hours_ge_0(self):
        with pytest.raises(Exception):
            VariantResourceAggregate(
                variant_id="v1", total_attempts=1, gpu_hours=-1, wall_time=100,
            )

    def test_wall_time_ge_0(self):
        with pytest.raises(Exception):
            VariantResourceAggregate(
                variant_id="v1", total_attempts=1, gpu_hours=1, wall_time=-1,
            )

    def test_total_attempts_ge_0(self):
        a = VariantResourceAggregate(
            variant_id="v1", total_attempts=0, gpu_hours=0, wall_time=0,
        )
        assert a.total_attempts == 0


class TestBaselineResourceAggregate:
    def test_valid(self):
        a = BaselineResourceAggregate(total_attempts=2, gpu_hours=4.0, wall_time=7200.0)
        assert a.gpu_hours == 4.0

    def test_negative_gpu_hours(self):
        with pytest.raises(Exception):
            BaselineResourceAggregate(total_attempts=1, gpu_hours=-1, wall_time=100)


class TestResourceDelta:
    def test_valid(self):
        d = ResourceDelta(
            variant_id="v1", delta_gpu_hours=2.0, delta_wall_time=3600.0,
        )
        assert d.delta_gpu_hours == 2.0


class TestVariantBudgetAssessment:
    def test_within_budget(self):
        a = VariantResourceAggregate(
            variant_id="v1", total_attempts=1, gpu_hours=2.0, wall_time=3600,
        )
        ba = VariantBudgetAssessment(
            variant_id="v1",
            resource_aggregate=a,
            budget_remaining=8.0,
            within_budget=True,
            reason="within budget",
        )
        assert ba.within_budget is True

    def test_over_budget(self):
        a = VariantResourceAggregate(
            variant_id="v1", total_attempts=3, gpu_hours=12.0, wall_time=21600,
        )
        ba = VariantBudgetAssessment(
            variant_id="v1",
            resource_aggregate=a,
            budget_remaining=0.0,
            within_budget=False,
            reason="exceeded budget",
        )
        assert ba.within_budget is False


class TestResourceComparisonReport:
    def test_minimal(self):
        r = ResourceComparisonReport()
        assert r.overall_within_budget is False
        assert r.variant_aggregates == []


class TestBundleResourceAggregate:
    def test_valid(self):
        b = BundleResourceAggregate(
            bundle_id="b1",
            total_gpu_hours=10.0,
            total_wall_time=36000.0,
        )
        assert b.total_gpu_hours == 10.0

    def test_negative_total(self):
        with pytest.raises(Exception):
            BundleResourceAggregate(
                bundle_id="b1", total_gpu_hours=-1, total_wall_time=0,
            )


class TestBundleBudgetAssessment:
    def test_valid(self):
        agg = BundleResourceAggregate(
            bundle_id="b1", total_gpu_hours=5.0, total_wall_time=18000,
        )
        ba = BundleBudgetAssessment(
            bundle_id="b1",
            bundle_aggregate=agg,
            budget_ref=_ref("budget"),
            within_budget=True,
            reason="within budget",
        )
        assert ba.within_budget is True


# ── Failure Analysis ──────────────────────────────────────────────────


class TestFailureAnalysis:
    def test_valid(self):
        fa = FailureAnalysis(failure_summary="all passed")
        assert fa.failure_summary == "all passed"
        assert fa.terminal_units == []


# ── Reflection ────────────────────────────────────────────────────────


class TestNextRunProposal:
    def test_valid(self):
        p = NextRunProposal(
            proposed_next_action="conclude_and_report",
            rationale="all variants tested",
            estimated_impact="low",
        )
        assert p.proposed_next_action == "conclude_and_report"


class TestReportFacts:
    def test_valid(self):
        f = ReportFacts(
            run_id="run_001",
            num_variants=2,
            num_successful=1,
            num_failed=1,
            total_gpu_hours=10.0,
            total_wall_time_seconds=36000.0,
        )
        assert f.total_gpu_hours == 10.0

    def test_negative_values(self):
        with pytest.raises(Exception):
            ReportFacts(
                run_id="run_001", num_variants=-1, num_successful=0,
                num_failed=0, total_gpu_hours=0, total_wall_time_seconds=0,
            )


class TestReflection:
    def test_minimal(self):
        r = Reflection()
        assert r.per_variant_conclusions == []
        assert r.resource_report is None
