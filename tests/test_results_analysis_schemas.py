"""Tests for 3.9 results analysis sealed schemas — model creation, validators, constraints."""

import math

import pytest

from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.experiment_planning import ScientificConclusion
from autoad_researcher.schemas.results_analysis import (
    AggregatedMetricComparison,
    AggregatedMetricKey,
    BaselineResourceAggregate,
    BundleBudgetAssessment,
    BundleResourceAggregate,
    CurrentRunBaselineMetricRef,
    EvidenceSufficiency,
    FailureAnalysis,
    IdeaSupportConclusion,
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


def _sha() -> str:
    return _SHA


def _ref(artifact_id: str = "art") -> ArtifactReferenceV2:
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type="report",
        locator=f"runs/run_test/{artifact_id}.json",
        sha256=_sha(),
    )


def _baseline_source(seed: int = 0) -> CurrentRunBaselineMetricRef:
    return CurrentRunBaselineMetricRef(
        metric_name="auroc",
        unit_id="u1",
        seed=seed,
        metric_ref=_ref("base_metric"),
        validity_ref=_ref("base_validity"),
    )


def _obs(
    seed: int = 0,
    baseline_value: float = 0.80,
    variant_value: float = 0.85,
    direction: str = "maximize",
) -> PairedMetricObservation:
    raw_delta = variant_value - baseline_value
    imp_delta = raw_delta if direction == "maximize" else baseline_value - variant_value
    rel = raw_delta / abs(baseline_value) * 100.0
    imp_rel = imp_delta / abs(baseline_value) * 100.0
    return PairedMetricObservation(
        seed=seed,
        baseline_source=_baseline_source(seed=seed),
        baseline_value=baseline_value,
        variant_unit_id="u1",
        variant_id="v1",
        variant_metric_ref=_ref("var_metric"),
        variant_value=variant_value,
        direction=direction,
        raw_delta=raw_delta,
        improvement_delta=imp_delta,
        raw_relative_change_pct=rel,
        improvement_relative_change_pct=imp_rel,
        pair_validity_status="valid",
        variant_validity_ref=_ref("var_validity"),
        baseline_validity_ref=_ref("base_validity"),
        protocol_fingerprint="fp1",
    )


def _baseline_agg(*, per_unit=None, measurement_status="measured"):
    _per_unit = per_unit if per_unit is not None else {"unit_baseline": 4.0}
    return BaselineResourceAggregate(
        attempt_report_refs=[_ref("usage_base")],
        per_unit_actual_gpu_hours=_per_unit,
        total_wall_time_seconds=3600.0,
        peak_gpu_memory_mb=1024.0,
        measurement_status=measurement_status,
    )


def _variant_agg(variant_id="v1", *, per_unit=None, measurement_status="measured"):
    _per_unit = per_unit if per_unit is not None else {"unit_001": 5.0}
    return VariantResourceAggregate(
        variant_id=variant_id,
        attempt_report_refs=[_ref("usage")],
        per_unit_actual_gpu_hours=_per_unit,
        total_wall_time_seconds=3600.0,
        peak_gpu_memory_mb=1024.0,
        measurement_status=measurement_status,
    )


def _bundle_agg(baseline=None, per_variant=None):
    return BundleResourceAggregate(
        baseline=baseline if baseline is not None else _baseline_agg(),
        per_variant=per_variant if per_variant is not None else {"v1": _variant_agg("v1")},
    )


def _budget_ba(status="within_budget", *, max_unit=5.0, bundle_total=10.0):
    if status == "not_assessable":
        return BundleBudgetAssessment(status="not_assessable", reason="not assessable")
    return BundleBudgetAssessment(
        status=status,
        max_unit_actual_gpu_hours=max_unit,
        bundle_total_actual_gpu_hours=bundle_total,
        resource_budget_ref=_ref("budget"),
        resource_usage_refs=[_ref("usage")],
        reason="ok",
    )


# ── Baseline Metric Refs ──────────────────────────────────────────────


class TestCurrentRunBaselineMetricRef:
    def test_valid(self):
        ref = CurrentRunBaselineMetricRef(
            metric_name="auroc",
            unit_id="u1",
            seed=0,
            metric_ref=_ref("base_metric"),
            validity_ref=_ref("base_validity"),
        )
        assert ref.source_type == "current_run"
        assert ref.metric_name == "auroc"


class TestReusedBaselineMetricRef:
    def test_valid(self):
        ref = ReusedBaselineMetricRef(
            metric_name="auroc",
            source_run_id="run_001",
            seed=0,
            metric_ref=_ref("base_metric"),
            validity_ref=_ref("base_validity"),
        )
        assert ref.source_type == "reused"
        assert ref.source_run_id == "run_001"


# ── Metric Keys ───────────────────────────────────────────────────────


class TestMetricObservationKey:
    def test_valid(self):
        k = MetricObservationKey(unit_id="u1", seed=0, role="variant")
        assert k.unit_id == "u1"
        assert k.seed == 0


class TestAggregatedMetricKey:
    def test_valid(self):
        k = AggregatedMetricKey(
            variant_id="v1", metric_name="auroc", dataset_row="bottle", direction="maximize",
        )
        assert k.metric_name == "auroc"
        assert k.direction == "maximize"


# ── PairedMetricObservation ───────────────────────────────────────────


class TestPairedMetricObservation:
    def test_maximize_direction(self):
        obs = PairedMetricObservation(
            seed=0,
            baseline_source=_baseline_source(),
            baseline_value=0.80,
            variant_unit_id="u1",
            variant_id="v1",
            variant_metric_ref=_ref("var_metric"),
            variant_value=0.85,
            direction="maximize",
            raw_delta=0.05,
            improvement_delta=0.05,
            raw_relative_change_pct=6.25,
            improvement_relative_change_pct=6.25,
            pair_validity_status="valid",
            variant_validity_ref=_ref("var_validity"),
            baseline_validity_ref=_ref("base_validity"),
            protocol_fingerprint="fp1",
        )
        assert obs.raw_delta == 0.05
        assert obs.improvement_delta == 0.05
        assert obs.raw_relative_change_pct == pytest.approx(6.25)

    def test_minimize_direction(self):
        obs = PairedMetricObservation(
            seed=0,
            baseline_source=_baseline_source(),
            baseline_value=100.0,
            variant_unit_id="u1",
            variant_id="v1",
            variant_metric_ref=_ref("var_metric"),
            variant_value=90.0,
            direction="minimize",
            raw_delta=-10.0,
            improvement_delta=10.0,
            raw_relative_change_pct=-10.0,
            improvement_relative_change_pct=10.0,
            pair_validity_status="valid",
            variant_validity_ref=_ref("var_validity"),
            baseline_validity_ref=_ref("base_validity"),
            protocol_fingerprint="fp1",
        )
        assert obs.raw_delta == -10.0
        assert obs.improvement_delta == 10.0

    def test_raw_delta_mismatch_raises(self):
        with pytest.raises(ValueError, match="raw_delta"):
            PairedMetricObservation(
                seed=0,
                baseline_source=_baseline_source(),
                baseline_value=0.80,
                variant_unit_id="u1",
                variant_id="v1",
                variant_metric_ref=_ref("var_metric"),
                variant_value=0.85,
                direction="maximize",
                raw_delta=0.99,
                improvement_delta=0.05,
                pair_validity_status="valid",
                variant_validity_ref=_ref("var_validity"),
                baseline_validity_ref=_ref("base_validity"),
                protocol_fingerprint="fp1",
            )

    def test_improvement_delta_mismatch_raises(self):
        with pytest.raises(ValueError, match="improvement_delta"):
            PairedMetricObservation(
                seed=0,
                baseline_source=_baseline_source(),
                baseline_value=100.0,
                variant_unit_id="u1",
                variant_id="v1",
                variant_metric_ref=_ref("var_metric"),
                variant_value=90.0,
                direction="minimize",
                raw_delta=-10.0,
                improvement_delta=0.0,
                pair_validity_status="valid",
                variant_validity_ref=_ref("var_validity"),
                baseline_validity_ref=_ref("base_validity"),
                protocol_fingerprint="fp1",
            )

    def test_relative_change_pct_mismatch_raises(self):
        with pytest.raises(ValueError, match="raw_relative_change_pct"):
            PairedMetricObservation(
                seed=0,
                baseline_source=_baseline_source(),
                baseline_value=0.80,
                variant_unit_id="u1",
                variant_id="v1",
                variant_metric_ref=_ref("var_metric"),
                variant_value=0.85,
                direction="maximize",
                raw_delta=0.05,
                improvement_delta=0.05,
                raw_relative_change_pct=99.0,
                improvement_relative_change_pct=6.25,
                pair_validity_status="valid",
                variant_validity_ref=_ref("var_validity"),
                baseline_validity_ref=_ref("base_validity"),
                protocol_fingerprint="fp1",
            )

    def test_baseline_zero_gives_none_relative_pcts(self):
        obs = PairedMetricObservation(
            seed=0,
            baseline_source=_baseline_source(),
            baseline_value=0.0,
            variant_unit_id="u1",
            variant_id="v1",
            variant_metric_ref=_ref("var_metric"),
            variant_value=0.05,
            direction="maximize",
            raw_delta=0.05,
            improvement_delta=0.05,
            raw_relative_change_pct=None,
            improvement_relative_change_pct=None,
            pair_validity_status="valid",
            variant_validity_ref=_ref("var_validity"),
            baseline_validity_ref=_ref("base_validity"),
            protocol_fingerprint="fp1",
        )
        assert obs.raw_relative_change_pct is None
        assert obs.improvement_relative_change_pct is None

    def test_baseline_near_zero_gives_none_relative_pcts(self):
        obs = PairedMetricObservation(
            seed=0,
            baseline_source=_baseline_source(),
            baseline_value=1e-11,
            variant_unit_id="u1",
            variant_id="v1",
            variant_metric_ref=_ref("var_metric"),
            variant_value=0.05,
            direction="maximize",
            raw_delta=0.05,
            improvement_delta=0.05,
            raw_relative_change_pct=None,
            improvement_relative_change_pct=None,
            pair_validity_status="valid",
            variant_validity_ref=_ref("var_validity"),
            baseline_validity_ref=_ref("base_validity"),
            protocol_fingerprint="fp1",
        )
        assert obs.raw_relative_change_pct is None

    def test_baseline_zero_with_non_none_pcts_raises(self):
        with pytest.raises(ValueError, match="should be None"):
            PairedMetricObservation(
                seed=0,
                baseline_source=_baseline_source(),
                baseline_value=0.0,
                variant_unit_id="u1",
                variant_id="v1",
                variant_metric_ref=_ref("var_metric"),
                variant_value=0.05,
                direction="maximize",
                raw_delta=0.05,
                improvement_delta=0.05,
                raw_relative_change_pct=5.0,
                improvement_relative_change_pct=None,
                pair_validity_status="valid",
                variant_validity_ref=_ref("var_validity"),
                baseline_validity_ref=_ref("base_validity"),
                protocol_fingerprint="fp1",
            )


# ── AggregatedMetricComparison ────────────────────────────────────────


class TestAggregatedMetricComparison:
    def test_minimal(self):
        comp = AggregatedMetricComparison(
            aggregate_key=AggregatedMetricKey(
                variant_id="v1", metric_name="auroc", dataset_row="bottle", direction="maximize",
            ),
            comparison_status="missing",
            seed_count=0,
            completed_seed_count=0,
        )
        assert comp.aggregate_key.metric_name == "auroc"
        assert comp.paired_observations == []

    def test_with_observations(self):
        obs = _obs(seed=0)
        comp = AggregatedMetricComparison(
            aggregate_key=AggregatedMetricKey(
                variant_id="v1", metric_name="auroc", dataset_row="bottle", direction="maximize",
            ),
            paired_observations=[obs],
            comparison_status="valid",
            seed_count=1,
            completed_seed_count=1,
            mean_baseline=0.80,
            mean_variant=0.85,
            mean_raw_delta=0.05,
            mean_improvement_delta=0.05,
        )
        assert len(comp.paired_observations) == 1
        assert comp.mean_raw_delta == 0.05

    def test_seed_count_must_equal_observations_length(self):
        obs = _obs(seed=0)
        with pytest.raises(ValueError, match="seed_count must equal len"):
            AggregatedMetricComparison(
                aggregate_key=AggregatedMetricKey(
                    variant_id="v1", metric_name="auroc", dataset_row="bottle", direction="maximize",
                ),
                paired_observations=[obs],
                comparison_status="valid",
                seed_count=2,
                completed_seed_count=1,
            )

    def test_duplicate_seed_raises(self):
        obs_0 = _obs(seed=0)
        obs_0_dup = _obs(seed=0, variant_value=0.86)
        with pytest.raises(ValueError, match="duplicate seed"):
            AggregatedMetricComparison(
                aggregate_key=AggregatedMetricKey(
                    variant_id="v1", metric_name="auroc", dataset_row="bottle", direction="maximize",
                ),
                paired_observations=[obs_0, obs_0_dup],
                comparison_status="valid",
                seed_count=2,
                completed_seed_count=2,
            )

    def test_correct_seed_count_with_distinct_seeds_passes(self):
        obs_0 = _obs(seed=0)
        obs_1 = _obs(seed=1, baseline_value=0.82, variant_value=0.87)
        comp = AggregatedMetricComparison(
            aggregate_key=AggregatedMetricKey(
                variant_id="v1", metric_name="auroc", dataset_row="bottle", direction="maximize",
            ),
            paired_observations=[obs_0, obs_1],
            comparison_status="valid",
            seed_count=2,
            completed_seed_count=2,
        )
        assert len(comp.paired_observations) == 2


# ── Resolved Evidence ─────────────────────────────────────────────────


class TestResolvedMetricEvidence:
    def test_valid_with_dict_payload(self):
        ev = ResolvedMetricEvidence(
            metric_ref=_ref("metric"),
            verified_sha256=_sha(),
            source_run_id="run_001",
            seed=0,
            metric={"name": "auroc", "value": 0.95},
        )
        assert ev.metric == {"name": "auroc", "value": 0.95}
        assert ev.source_run_id == "run_001"


class TestResolvedValidityEvidence:
    def test_valid_with_dict_payload(self):
        ev = ResolvedValidityEvidence(
            validity_ref=_ref("validity"),
            verified_sha256=_sha(),
            source_run_id="run_001",
            seed=0,
            report={"overall_valid": True},
        )
        assert ev.report == {"overall_valid": True}


# ── Evidence Sufficiency ──────────────────────────────────────────────


class TestEvidenceSufficiency:
    def test_valid(self):
        es = EvidenceSufficiency(
            variant_id="v1",
            total_planned_seeds=5,
            completed_seed_pairs=5,
            valid_seed_pairs=5,
            metric_count=3,
            valid_metric_count=3,
            protocol_fingerprint="fp1",
        )
        assert es.total_planned_seeds == 5

    def test_valid_le_completed_le_planned_enforced(self):
        with pytest.raises(ValueError, match="valid_seed_pairs"):
            EvidenceSufficiency(
                variant_id="v1",
                total_planned_seeds=5,
                completed_seed_pairs=3,
                valid_seed_pairs=4,
                metric_count=3,
                valid_metric_count=3,
                protocol_fingerprint="fp1",
            )

    def test_completed_le_planned_enforced(self):
        with pytest.raises(ValueError, match="completed_seed_pairs"):
            EvidenceSufficiency(
                variant_id="v1",
                total_planned_seeds=3,
                completed_seed_pairs=5,
                valid_seed_pairs=3,
                metric_count=3,
                valid_metric_count=3,
                protocol_fingerprint="fp1",
            )

    def test_valid_metric_le_metric_count_enforced(self):
        with pytest.raises(ValueError, match="valid_metric_count"):
            EvidenceSufficiency(
                variant_id="v1",
                total_planned_seeds=5,
                completed_seed_pairs=5,
                valid_seed_pairs=5,
                metric_count=2,
                valid_metric_count=3,
                protocol_fingerprint="fp1",
            )


# ── VariantScientificConclusion ───────────────────────────────────────


class TestVariantScientificConclusion:
    def test_enum_values(self):
        for val in ScientificConclusion:
            c = VariantScientificConclusion(
                variant_id="v1",
                conclusion=val,
                matched_rule_id="rule_01",
            )
            assert c.conclusion == val


# ── IdeaSupportConclusion ─────────────────────────────────────────────


class TestIdeaSupportConclusion:
    def test_enum_values(self):
        values = {e.value for e in IdeaSupportConclusion}
        assert "consistently_supported" in values
        assert "not_supported_by_tested_variants" in values
        assert "cannot_judge" in values


# ── Replication ───────────────────────────────────────────────────────


class TestReplicationPairEvidence:
    def test_valid(self):
        pair = ReplicationPairEvidence(
            pair_id="p1",
            seed=0,
            paired_observation_ref=_ref("pair_obs"),
            improvement_delta=0.05,
            validity_status="valid",
        )
        assert pair.pair_id == "p1"


class TestReplicationGroup:
    def test_valid(self):
        g = ReplicationGroup(
            group_id="g1",
            variant_id="v1",
            overall_status="reproducible",
        )
        assert g.group_id == "g1"
        assert g.overall_status == "reproducible"


class TestReproducibilityInterpretation:
    def test_valid(self):
        r = ReproducibilityInterpretation(
            groups=[
                ReplicationGroup(
                    group_id="g1",
                    variant_id="v1",
                    overall_status="reproducible",
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


# ── Resource Aggregates ───────────────────────────────────────────────


class TestVariantResourceAggregate:
    def test_valid(self):
        a = VariantResourceAggregate(
            variant_id="v1",
            per_unit_actual_gpu_hours={"unit_a": 3.0, "unit_b": 2.0},
            total_wall_time_seconds=3600.0,
            peak_gpu_memory_mb=8192.0,
            measurement_status="measured",
        )
        assert a.variant_id == "v1"
        assert a.total_actual_gpu_hours == 5.0

    def test_computed_total_summed_from_dict(self):
        a = VariantResourceAggregate(
            variant_id="v1",
            per_unit_actual_gpu_hours={"u1": 1.5, "u2": 2.5, "u3": 3.0},
            measurement_status="measured",
        )
        assert a.total_actual_gpu_hours == 7.0

    def test_empty_dict_total_zero(self):
        a = VariantResourceAggregate(
            variant_id="v1",
            per_unit_actual_gpu_hours={},
            measurement_status="not_available",
        )
        assert a.total_actual_gpu_hours == 0.0

    def test_rejects_negative_gpu_hours(self):
        with pytest.raises(ValueError, match="invalid GPU-hours"):
            VariantResourceAggregate(
                variant_id="v1",
                per_unit_actual_gpu_hours={"u1": -1.0},
                measurement_status="measured",
            )

    def test_rejects_nan_gpu_hours(self):
        with pytest.raises(ValueError, match="invalid GPU-hours"):
            VariantResourceAggregate(
                variant_id="v1",
                per_unit_actual_gpu_hours={"u1": math.nan},
                measurement_status="measured",
            )

    def test_rejects_inf_gpu_hours(self):
        with pytest.raises(ValueError, match="invalid GPU-hours"):
            VariantResourceAggregate(
                variant_id="v1",
                per_unit_actual_gpu_hours={"u1": math.inf},
                measurement_status="measured",
            )


class TestBaselineResourceAggregate:
    def test_valid(self):
        a = BaselineResourceAggregate(
            per_unit_actual_gpu_hours={"unit_a": 4.0},
            total_wall_time_seconds=7200.0,
            peak_gpu_memory_mb=16384.0,
            measurement_status="measured",
        )
        assert a.total_actual_gpu_hours == 4.0

    def test_computed_total_summed_from_dict(self):
        a = BaselineResourceAggregate(
            per_unit_actual_gpu_hours={"u1": 2.0, "u2": 3.0},
            measurement_status="measured",
        )
        assert a.total_actual_gpu_hours == 5.0

    def test_rejects_negative_gpu_hours(self):
        with pytest.raises(ValueError, match="invalid GPU-hours"):
            BaselineResourceAggregate(
                per_unit_actual_gpu_hours={"u1": -2.0},
                measurement_status="measured",
            )


class TestResourceDelta:
    def test_valid(self):
        d = ResourceDelta(
            variant_id="v1",
            wall_time_delta_seconds=3600.0,
            gpu_memory_delta_mb=1024.0,
            measurement_compatible=True,
        )
        assert d.variant_id == "v1"
        assert d.measurement_compatible is True


class TestBundleResourceAggregate:
    def test_valid(self):
        baseline = BaselineResourceAggregate(
            per_unit_actual_gpu_hours={"u1": 2.0},
            measurement_status="measured",
        )
        variant = VariantResourceAggregate(
            variant_id="v1",
            per_unit_actual_gpu_hours={"u1": 3.0},
            measurement_status="measured",
        )
        b = BundleResourceAggregate(
            baseline=baseline,
            per_variant={"v1": variant},
        )
        assert b.total_actual_gpu_hours == 5.0
        assert b.max_unit_actual_gpu_hours == 3.0

    def test_computed_fields(self):
        baseline = BaselineResourceAggregate(
            per_unit_actual_gpu_hours={"u1": 1.0, "u2": 2.0},
            measurement_status="measured",
        )
        v1 = VariantResourceAggregate(
            variant_id="v1",
            per_unit_actual_gpu_hours={"u1": 4.0, "u2": 1.0},
            measurement_status="measured",
        )
        v2 = VariantResourceAggregate(
            variant_id="v2",
            per_unit_actual_gpu_hours={"u2": 5.0},
            measurement_status="measured",
        )
        b = BundleResourceAggregate(
            baseline=baseline,
            per_variant={"v1": v1, "v2": v2},
        )
        assert b.total_actual_gpu_hours == 13.0
        assert b.max_unit_actual_gpu_hours == 5.0


# ── Budget Assessments ────────────────────────────────────────────────


class TestBundleBudgetAssessment:
    def test_within_budget_valid(self):
        ba = BundleBudgetAssessment(
            status="within_budget",
            max_unit_actual_gpu_hours=5.0,
            bundle_total_actual_gpu_hours=10.0,
            resource_budget_ref=_ref("budget"),
            resource_usage_refs=[_ref("usage")],
            reason="all ok",
        )
        assert ba.status == "within_budget"

    def test_coverage_issues_force_not_assessable(self):
        ba = BundleBudgetAssessment(
            status="not_assessable",
            resource_usage_refs=[_ref("usage")],
            missing_unit_ids=["u_missing"],
            reason="missing unit",
        )
        assert ba.status == "not_assessable"

    def test_coverage_issues_disallow_assessable_statuses(self):
        with pytest.raises(ValueError, match="not_assessable"):
            BundleBudgetAssessment(
                status="within_budget",
                max_unit_actual_gpu_hours=5.0,
                bundle_total_actual_gpu_hours=10.0,
                resource_budget_ref=_ref("budget"),
                resource_usage_refs=[_ref("usage")],
                missing_variant_ids=["v_missing"],
                reason="missing variant",
            )

    def test_assessable_requires_refs(self):
        with pytest.raises(ValueError, match="resource_budget_ref"):
            BundleBudgetAssessment(
                status="within_budget",
                max_unit_actual_gpu_hours=5.0,
                bundle_total_actual_gpu_hours=10.0,
                resource_usage_refs=[_ref("usage")],
                reason="missing ref",
            )

    def test_assessable_requires_max_unit_not_none(self):
        with pytest.raises(ValueError, match="max_unit_actual_gpu_hours"):
            BundleBudgetAssessment(
                status="within_budget",
                max_unit_actual_gpu_hours=None,
                bundle_total_actual_gpu_hours=10.0,
                resource_budget_ref=_ref("budget"),
                resource_usage_refs=[_ref("usage")],
                reason="missing max",
            )

    def test_assessable_requires_bundle_total_not_none(self):
        with pytest.raises(ValueError, match="bundle_total_actual_gpu_hours"):
            BundleBudgetAssessment(
                status="within_budget",
                max_unit_actual_gpu_hours=5.0,
                bundle_total_actual_gpu_hours=None,
                resource_budget_ref=_ref("budget"),
                resource_usage_refs=[_ref("usage")],
                reason="missing total",
            )

    def test_not_assessable_allows_none_refs(self):
        ba = BundleBudgetAssessment(
            status="not_assessable",
            reason="no telemetry",
        )
        assert ba.status == "not_assessable"
        assert ba.resource_budget_ref is None


class TestVariantBudgetAssessment:
    def test_within_budget_valid(self):
        ba = VariantBudgetAssessment(
            variant_id="v1",
            status="within_budget",
            reason="within budget",
            resource_budget_ref=_ref("budget"),
            resource_usage_refs=[_ref("usage")],
        )
        assert ba.status == "within_budget"

    def test_within_budget_requires_ref(self):
        with pytest.raises(ValueError, match="resource_budget_ref"):
            VariantBudgetAssessment(
                variant_id="v1",
                status="within_budget",
                reason="missing ref",
            )

    def test_within_budget_requires_usage_refs(self):
        with pytest.raises(ValueError, match="resource_usage_refs"):
            VariantBudgetAssessment(
                variant_id="v1",
                status="within_budget",
                reason="missing usage",
                resource_budget_ref=_ref("budget"),
            )

    def test_not_assessable_allows_none_ref(self):
        ba = VariantBudgetAssessment(
            variant_id="v1",
            status="not_assessable",
            reason="no telemetry",
        )
        assert ba.resource_budget_ref is None


# ── Resource Comparison Report ─────────────────────────────────────────


class TestResourceComparisonReport:
    def test_minimal(self):
        baseline = _baseline_agg()
        bundle = _bundle_agg(baseline=baseline, per_variant={})
        budget_ba = _budget_ba()
        r = ResourceComparisonReport(
            baseline=baseline,
            bundle=bundle,
            bundle_budget_assessment=budget_ba,
        )
        assert r.baseline is baseline
        assert r.per_variant == {}
        assert r.per_variant_deltas == {}
        assert r.per_variant_budget_assessments == {}
        assert r.bundle is bundle
        assert r.bundle_budget_assessment is budget_ba
        assert r.evidence_refs == []

    def test_dict_based_structure(self):
        baseline = BaselineResourceAggregate(
            per_unit_actual_gpu_hours={"u1": 2.0},
            measurement_status="measured",
        )
        v1 = VariantResourceAggregate(
            variant_id="v1",
            per_unit_actual_gpu_hours={"u1": 3.0},
            measurement_status="measured",
        )
        delta = ResourceDelta(
            variant_id="v1",
            wall_time_delta_seconds=600.0,
            measurement_compatible=True,
        )
        va = VariantBudgetAssessment(
            variant_id="v1",
            status="within_budget",
            reason="ok",
            resource_budget_ref=_ref("budget"),
            resource_usage_refs=[_ref("usage")],
        )
        bundle = BundleResourceAggregate(
            baseline=baseline,
            per_variant={"v1": v1},
        )
        ba = BundleBudgetAssessment(
            status="within_budget",
            max_unit_actual_gpu_hours=3.0,
            bundle_total_actual_gpu_hours=5.0,
            resource_budget_ref=_ref("budget"),
            resource_usage_refs=[_ref("usage")],
            reason="ok",
        )
        r = ResourceComparisonReport(
            baseline=baseline,
            per_variant={"v1": v1},
            per_variant_deltas={"v1": delta},
            per_variant_budget_assessments={"v1": va},
            bundle=bundle,
            bundle_budget_assessment=ba,
            evidence_refs=[_ref("ev")],
        )
        assert r.baseline is baseline
        assert r.per_variant["v1"] is v1
        assert r.per_variant_deltas["v1"] is delta
        assert r.per_variant_budget_assessments["v1"] is va
        assert r.bundle is bundle
        assert r.bundle_budget_assessment is ba
        assert len(r.evidence_refs) == 1

    def test_bundle_consistency_matching_passes(self):
        baseline = _baseline_agg()
        v1 = _variant_agg("v1")
        bundle = _bundle_agg(baseline=baseline, per_variant={"v1": v1})
        budget_ba = _budget_ba()
        delta = ResourceDelta(variant_id="v1", measurement_compatible=True)
        va = VariantBudgetAssessment(
            variant_id="v1", status="not_assessable", reason="no data",
        )
        r = ResourceComparisonReport(
            baseline=baseline,
            per_variant={"v1": v1},
            per_variant_deltas={"v1": delta},
            per_variant_budget_assessments={"v1": va},
            bundle=bundle,
            bundle_budget_assessment=budget_ba,
        )
        assert r.bundle is not None

    def test_bundle_consistency_mismatched_baseline_raises(self):
        baseline_report = _baseline_agg(per_unit={"u1": 2.0})
        baseline_bundle = _baseline_agg(per_unit={"u1": 5.0})
        bundle = BundleResourceAggregate(baseline=baseline_bundle)
        budget_ba = _budget_ba()
        with pytest.raises(ValueError, match="bundle baseline mismatch"):
            ResourceComparisonReport(
                baseline=baseline_report,
                bundle=bundle,
                bundle_budget_assessment=budget_ba,
            )

    def test_bundle_consistency_mismatched_per_variant_raises(self):
        baseline = _baseline_agg()
        v1 = _variant_agg("v1")
        bundle = BundleResourceAggregate(
            baseline=baseline,
            per_variant={"v1": v1},
        )
        budget_ba = _budget_ba()
        with pytest.raises(ValueError, match="bundle per_variant mismatch"):
            ResourceComparisonReport(
                baseline=baseline,
                per_variant={},
                bundle=bundle,
                bundle_budget_assessment=budget_ba,
            )


    def test_per_variant_keys_must_match_deltas(self):
        baseline = _baseline_agg()
        v1 = _variant_agg("v1")
        bundle = _bundle_agg(baseline=baseline, per_variant={"v1": v1})
        budget_ba = _budget_ba()
        va = VariantBudgetAssessment(
            variant_id="v1", status="not_assessable", reason="no data",
        )
        with pytest.raises(ValueError, match="per_variant keys != per_variant_deltas keys"):
            ResourceComparisonReport(
                baseline=baseline,
                per_variant={"v1": v1},
                per_variant_budget_assessments={"v1": va},
                bundle=bundle,
                bundle_budget_assessment=budget_ba,
            )

    def test_per_variant_keys_must_match_assessments(self):
        baseline = _baseline_agg()
        v1 = _variant_agg("v1")
        bundle = _bundle_agg(baseline=baseline, per_variant={"v1": v1})
        budget_ba = _budget_ba()
        delta = ResourceDelta(variant_id="v1", measurement_compatible=True)
        with pytest.raises(ValueError, match="per_variant keys != per_variant_budget_assessments keys"):
            ResourceComparisonReport(
                baseline=baseline,
                per_variant={"v1": v1},
                per_variant_deltas={"v1": delta},
                bundle=bundle,
                bundle_budget_assessment=budget_ba,
            )

    def test_variant_id_must_match_key(self):
        baseline = _baseline_agg()
        v2 = _variant_agg("v2")
        bundle = _bundle_agg(baseline=baseline, per_variant={"v1": v2})
        budget_ba = _budget_ba()
        delta = ResourceDelta(variant_id="v1", measurement_compatible=True)
        va = VariantBudgetAssessment(
            variant_id="v1", status="not_assessable", reason="no data",
        )
        with pytest.raises(ValueError, match=r"per_variant\[v1\]\.variant_id=v2 != key v1"):
            ResourceComparisonReport(
                baseline=baseline,
                per_variant={"v1": v2},
                per_variant_deltas={"v1": delta},
                per_variant_budget_assessments={"v1": va},
                bundle=bundle,
                bundle_budget_assessment=budget_ba,
            )


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

    def test_negative_values_raise(self):
        with pytest.raises(Exception):
            ReportFacts(
                run_id="run_001",
                num_variants=-1,
                num_successful=0,
                num_failed=0,
                total_gpu_hours=0,
                total_wall_time_seconds=0,
            )


class TestReflection:
    def test_minimal(self):
        r = Reflection()
        assert r.per_variant_conclusions == []
        assert r.resource_report is None
