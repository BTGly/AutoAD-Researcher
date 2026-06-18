"""Tests for 3.9 analysis service functions — deltas, crossval, conclusions, budget."""

import pytest

from autoad_researcher.analysis.budget import (
    compare_per_experiment_usage,
    determine_budget_assessment,
    determine_bundle_budget_assessment,
    validate_bundle_resource_coverage,
    validate_resource_comparison_report,
)
from autoad_researcher.analysis.conclusions import derive_idea_support
from autoad_researcher.analysis.crossval import (
    derive_pair_validity,
    validate_aggregate_from_observations,
    validate_observation_against_metric_artifacts,
)
from autoad_researcher.analysis.delta import compute_deltas
from autoad_researcher.analysis.metrics import ParsedMetric
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2, ResolvedArtifact
from autoad_researcher.schemas.execution import (
    ExecutionManifest,
    ExecutionUnitRecord,
    ExecutionUnitStatus,
)
from autoad_researcher.schemas.experiment_planning import (
    BudgetDecision,
    ExperimentBundleResourceBudget,
    ResourceBudget,
    ResourceLimits,
    ScientificConclusion,
    VariantResourceSummary,
)
from autoad_researcher.schemas.results_analysis import (
    AggregatedMetricComparison,
    AggregatedMetricKey,
    BaselineResourceAggregate,
    BundleBudgetAssessment,
    BundleResourceAggregate,
    CurrentRunBaselineMetricRef,
    IdeaSupportConclusion,
    PairedMetricObservation,
    ResolvedMetricEvidence,
    ResolvedValidityEvidence,
    ResourceComparisonReport,
    ResourceDelta,
    ReusedBaselineMetricRef,
    VariantBudgetAssessment,
    VariantResourceAggregate,
    VariantScientificConclusion,
)
from autoad_researcher.supervisor.validity import (
    ScientificValidityReport,
    ValidityCheck,
)

_SHA = "a" * 64
_FP = "fp_v1"


def _ref(artifact_id="art"):
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type="report",
        locator=f"runs/run_test/{artifact_id}.json",
        sha256=_SHA,
    )


def _ref_sha(sha, artifact_id="art"):
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type="report",
        locator=f"runs/run_test/{artifact_id}.json",
        sha256=sha,
    )


def _validity(status="valid", *, sha=_SHA):
    return ScientificValidityReport(
        schema_version=1,
        status=status,
        checks=[ValidityCheck(check_id="chk1", status="passed", message="ok")],
    )


def _parsed_metric(
    metric_name="auroc",
    dataset_row="bottle",
    value=0.90,
    unit="ratio",
    *,
    parse_status="parsed",
):
    return ParsedMetric(
        metric_name=metric_name,
        source_path="output.json",
        source_sha256=_SHA,
        dataset_row=dataset_row,
        value=value,
        unit=unit,
        required=True,
        parse_status=parse_status,
    )


def _metric_evidence(
    metric_ref=None,
    sha=_SHA,
    source_run_id="run_test",
    unit_id="unit_001",
    seed=42,
    *,
    metric_name="auroc",
    dataset_row="bottle",
    value=0.90,
):
    if metric_ref is None:
        metric_ref = _ref_sha(sha)
    pm = _parsed_metric(
        metric_name=metric_name,
        dataset_row=dataset_row,
        value=value,
    )
    return ResolvedMetricEvidence(
        metric_ref=metric_ref,
        verified_sha256=sha,
        source_run_id=source_run_id,
        unit_id=unit_id,
        seed=seed,
        metric=pm.model_dump(mode="json"),
    )


def _validity_evidence(
    validity_ref=None,
    sha=_SHA,
    source_run_id="run_test",
    unit_id="unit_001",
    seed=42,
    *,
    status="valid",
):
    if validity_ref is None:
        validity_ref = _ref_sha(sha)
    v = _validity(status, sha=sha)
    return ResolvedValidityEvidence(
        validity_ref=validity_ref,
        verified_sha256=sha,
        source_run_id=source_run_id,
        unit_id=unit_id,
        seed=seed,
        report=v.model_dump(mode="json"),
    )


def _paired_obs(
    seed=42,
    *,
    baseline_value=0.90,
    variant_value=0.95,
    direction="maximize",
    variant_id="v1",
    bl_sha=_SHA,
    var_sha=_SHA,
    bl_validity_sha=_SHA,
    var_validity_sha=_SHA,
    protocol_fingerprint=_FP,
):
    raw = variant_value - baseline_value
    if direction == "maximize":
        imp = raw
    else:
        imp = baseline_value - variant_value
    abs_base = abs(baseline_value)
    if abs_base < 1e-10:
        raw_pct = None
        imp_pct = None
    else:
        raw_pct = raw / abs_base * 100.0
        imp_pct = imp / abs_base * 100.0

    bl_source = ReusedBaselineMetricRef(
        metric_name="auroc",
        source_run_id="run_test",
        seed=seed,
        metric_ref=_ref_sha(bl_sha, "bl_metric"),
        validity_ref=_ref_sha(bl_validity_sha, "bl_validity"),
    )
    bl_val_ref = _ref_sha(bl_validity_sha, "bl_validity")
    var_val_ref = _ref_sha(var_validity_sha, "var_validity")

    return PairedMetricObservation(
        seed=seed,
        baseline_source=bl_source,
        baseline_value=baseline_value,
        variant_unit_id="unit_001",
        variant_id=variant_id,
        variant_metric_ref=_ref_sha(var_sha, "var_metric"),
        variant_value=variant_value,
        direction=direction,
        raw_delta=raw,
        improvement_delta=imp,
        raw_relative_change_pct=raw_pct,
        improvement_relative_change_pct=imp_pct,
        pair_validity_status="valid",
        variant_validity_ref=var_val_ref,
        baseline_validity_ref=bl_val_ref,
        protocol_fingerprint=protocol_fingerprint,
    )


def _agg_key(variant_id="v1", metric_name="auroc", dataset_row="bottle", direction="maximize"):
    return AggregatedMetricKey(
        variant_id=variant_id,
        metric_name=metric_name,
        dataset_row=dataset_row,
        direction=direction,
    )


def _make_budget(
    max_total=100.0,
    max_per_experiment=10.0,
    *,
    budget_id="budget_1",
):
    limits = ResourceLimits(
        max_total_gpu_hours=max_total,
        max_per_experiment_gpu_hours=max_per_experiment,
        available_gpu_count=4,
        available_gpu_type="A100",
    )
    bundle_est = ExperimentBundleResourceBudget(
        total_gpu_hours=80.0,
        total_wall_clock_hours=200.0,
        max_single_experiment_gpu_hours=8.0,
    )
    decision = BudgetDecision(
        status="within_budget",
        original_limits=limits,
        estimated_consumption=bundle_est,
        utilization_pct=80.0,
    )
    return ResourceBudget(
        budget_id=budget_id,
        schema_version=1,
        protocol_fingerprint=_FP,
        protocol_version=1,
        limits=limits,
        per_variant={},
        total_estimate=bundle_est,
        budget_decision=decision,
    )


def _usage_ref(artifact_id="usage"):
    return _ref(artifact_id)


def _variant_agg(
    variant_id="v1",
    *,
    per_unit=None,
    measurement_status="measured",
    usage_refs=None,
):
    _per_unit = per_unit if per_unit is not None else {"unit_001": 5.0}
    return VariantResourceAggregate(
        variant_id=variant_id,
        attempt_report_refs=usage_refs if usage_refs is not None else [_usage_ref("usage")],
        per_unit_actual_gpu_hours=_per_unit,
        total_wall_time_seconds=3600.0,
        peak_gpu_memory_mb=1024.0,
        measurement_status=measurement_status,
    )


def _baseline_agg(*, per_unit=None, measurement_status="measured"):
    _per_unit = per_unit if per_unit is not None else {"unit_baseline": 4.0}
    return BaselineResourceAggregate(
        attempt_report_refs=[_usage_ref("usage_base")],
        per_unit_actual_gpu_hours=_per_unit,
        total_wall_time_seconds=3600.0,
        peak_gpu_memory_mb=1024.0,
        measurement_status=measurement_status,
    )


def _bundle_agg(baseline=None, per_variant=None):
    return BundleResourceAggregate(
        baseline=baseline or _baseline_agg(),
        per_variant=per_variant or {"v1": _variant_agg("v1")},
    )


def _exec_manifest(run_id="run_test", *, unit_records=None):
    recs = unit_records or []
    return ExecutionManifest(
        run_id=run_id,
        experiment_matrix_sha256=_SHA,
        protocol_fingerprint=_FP,
        workspace_refs_sha256=_SHA,
        operational_guard_policy_sha256=_SHA,
        runner_intake_report_ref=_ref("intake"),
        unit_records=recs,
        completed_unit_count=sum(1 for r in recs if r.final_status == ExecutionUnitStatus.COMPLETED),
        failed_unit_count=sum(1 for r in recs if r.final_status == ExecutionUnitStatus.FAILED),
        blocked_unit_count=sum(1 for r in recs if r.final_status == ExecutionUnitStatus.BLOCKED),
    )


def _unit_record(
    unit_id="unit_001",
    *,
    variant_id=None,
    seed=None,
    final_status=ExecutionUnitStatus.BLOCKED,
    terminal_reason="blocked_upstream_failure",
):
    return ExecutionUnitRecord(
        unit_id=unit_id,
        matrix_entry_id="entry_001",
        variant_id=variant_id,
        seed=seed,
        stage="main",
        workspace_id="ws_001",
        final_status=final_status,
        terminal_reason=terminal_reason,
        blocking_unit_ids=["blocker_001"] if terminal_reason == "blocked_upstream_failure" else [],
    )


def _scientific_conclusion(
    variant_id="v1",
    conclusion=ScientificConclusion.BENEFICIAL,
    *,
    matched_rule_id="rule_1",
):
    return VariantScientificConclusion(
        variant_id=variant_id,
        conclusion=conclusion,
        matched_rule_id=matched_rule_id,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. compute_deltas
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeDeltas:
    def test_maximize(self):
        raw, imp, raw_pct, imp_pct = compute_deltas(0.90, 0.95, "maximize")
        assert raw == pytest.approx(0.05)
        assert imp == pytest.approx(0.05)

    def test_minimize_improvement_positive(self):
        raw, imp, _raw_pct, _imp_pct = compute_deltas(0.50, 0.45, "minimize")
        assert raw == pytest.approx(-0.05)
        assert imp == pytest.approx(0.05)

    def test_relative_pct_maximize(self):
        _raw, _imp, raw_pct, imp_pct = compute_deltas(0.90, 0.95, "maximize")
        assert raw_pct == pytest.approx(5.555555, rel=0.001)
        assert imp_pct == pytest.approx(5.555555, rel=0.001)

    def test_relative_pct_minimize(self):
        _raw, _imp, raw_pct, imp_pct = compute_deltas(0.50, 0.45, "minimize")
        assert raw_pct == pytest.approx(-10.0)
        assert imp_pct == pytest.approx(10.0)

    def test_baseline_zero_gives_none_relative(self):
        _raw, _imp, raw_pct, imp_pct = compute_deltas(0.0, 0.05, "maximize")
        assert raw_pct is None
        assert imp_pct is None

    def test_baseline_negative_abs_handles_relative(self):
        _raw, _imp, raw_pct, imp_pct = compute_deltas(-0.50, 0.00, "maximize")
        assert raw_pct == pytest.approx(100.0)
        assert imp_pct == pytest.approx(100.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. derive_pair_validity
# ═══════════════════════════════════════════════════════════════════════════════


class TestDerivePairValidity:
    def test_all_valid(self):
        bv = _validity("valid")
        vv = _validity("valid")
        bm = _parsed_metric(parse_status="parsed")
        vm = _parsed_metric(parse_status="parsed")
        assert derive_pair_validity(bv, vv, bm, vm) == "valid"

    def test_baseline_validity_invalid(self):
        bv = _validity("invalid")
        vv = _validity("valid")
        bm = _parsed_metric(parse_status="parsed")
        vm = _parsed_metric(parse_status="parsed")
        assert derive_pair_validity(bv, vv, bm, vm) == "invalid"

    def test_variant_validity_invalid(self):
        bv = _validity("valid")
        vv = _validity("invalid")
        bm = _parsed_metric(parse_status="parsed")
        vm = _parsed_metric(parse_status="parsed")
        assert derive_pair_validity(bv, vv, bm, vm) == "invalid"

    def test_insufficient_evidence(self):
        bv = _validity("insufficient_evidence")
        vv = _validity("valid")
        bm = _parsed_metric(parse_status="parsed")
        vm = _parsed_metric(parse_status="parsed")
        assert derive_pair_validity(bv, vv, bm, vm) == "insufficient_evidence"

    def test_parse_status_not_parsed_returns_invalid(self):
        bv = _validity("valid")
        vv = _validity("valid")
        bm = _parsed_metric(parse_status="missing")
        vm = _parsed_metric(parse_status="parsed")
        assert derive_pair_validity(bv, vv, bm, vm) == "invalid"

    def test_variant_parse_status_not_parsed(self):
        bv = _validity("valid")
        vv = _validity("valid")
        bm = _parsed_metric(parse_status="parsed")
        vm = _parsed_metric(parse_status="invalid")
        assert derive_pair_validity(bv, vv, bm, vm) == "invalid"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. validate_observation_against_metric_artifacts
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateObservationAgainstMetricArtifacts:
    RUN_ID = "run_test"

    def test_happy_path(self):
        obs = _paired_obs(42, baseline_value=0.90, variant_value=0.95)
        key = _agg_key()
        bl_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, unit_id="unit_001", seed=42, value=0.90)
        var_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, unit_id="unit_001", seed=42, value=0.95)
        bl_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, unit_id="unit_001", seed=42, status="valid")
        var_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, unit_id="unit_001", seed=42, status="valid")
        validate_observation_against_metric_artifacts(obs, key, self.RUN_ID, _FP, bl_me, var_me, bl_ve, var_ve)

    def test_protocol_fingerprint_mismatch_raises(self):
        obs = _paired_obs(42, baseline_value=0.90, variant_value=0.95, protocol_fingerprint="fp_wrong")
        key = _agg_key()
        bl_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, unit_id="unit_001", seed=42, value=0.90)
        var_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, unit_id="unit_001", seed=42, value=0.95)
        bl_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, unit_id="unit_001", seed=42, status="valid")
        var_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, unit_id="unit_001", seed=42, status="valid")
        with pytest.raises(ValueError, match="protocol_fingerprint"):
            validate_observation_against_metric_artifacts(obs, key, self.RUN_ID, _FP, bl_me, var_me, bl_ve, var_ve)

    def test_baseline_metric_sha_mismatch_raises(self):
        bad_sha = "b" * 64
        obs = _paired_obs(42, baseline_value=0.90, variant_value=0.95, bl_sha=_SHA)
        key = _agg_key()
        bl_me = _metric_evidence(sha=bad_sha, source_run_id=self.RUN_ID, seed=42, value=0.90)
        var_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, value=0.95)
        bl_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        var_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        with pytest.raises(ValueError, match="sha256 mismatch"):
            validate_observation_against_metric_artifacts(obs, key, self.RUN_ID, _FP, bl_me, var_me, bl_ve, var_ve)

    def test_variant_metric_sha_mismatch_raises(self):
        bad_sha = "b" * 64
        obs = _paired_obs(42, baseline_value=0.90, variant_value=0.95, var_sha=_SHA)
        key = _agg_key()
        bl_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, value=0.90)
        var_me = _metric_evidence(sha=bad_sha, source_run_id=self.RUN_ID, seed=42, value=0.95)
        bl_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        var_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        with pytest.raises(ValueError, match="sha256 mismatch"):
            validate_observation_against_metric_artifacts(obs, key, self.RUN_ID, _FP, bl_me, var_me, bl_ve, var_ve)

    def test_seed_mismatch_raises(self):
        obs = _paired_obs(42, baseline_value=0.90, variant_value=0.95)
        key = _agg_key()
        bl_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=99, value=0.90)
        var_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, value=0.95)
        bl_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        var_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        with pytest.raises(ValueError, match="seed"):
            validate_observation_against_metric_artifacts(obs, key, self.RUN_ID, _FP, bl_me, var_me, bl_ve, var_ve)

    def test_run_id_mismatch_raises(self):
        obs = _paired_obs(42, baseline_value=0.90, variant_value=0.95)
        key = _agg_key()
        bl_me = _metric_evidence(sha=_SHA, source_run_id="wrong_run", seed=42, value=0.90)
        var_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, value=0.95)
        bl_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        var_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        with pytest.raises(ValueError, match="source_run_id"):
            validate_observation_against_metric_artifacts(obs, key, self.RUN_ID, _FP, bl_me, var_me, bl_ve, var_ve)

    def test_metric_value_mismatch_raises(self):
        obs = _paired_obs(42, baseline_value=0.90, variant_value=0.95)
        key = _agg_key()
        bl_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, value=0.99)
        var_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, value=0.95)
        bl_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        var_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        with pytest.raises(ValueError, match="mismatch"):
            validate_observation_against_metric_artifacts(obs, key, self.RUN_ID, _FP, bl_me, var_me, bl_ve, var_ve)

    def test_validity_ref_sha_mismatch_raises(self):
        bad_sha = "b" * 64
        obs = _paired_obs(42, baseline_value=0.90, variant_value=0.95, bl_validity_sha=_SHA)
        key = _agg_key()
        bl_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, value=0.90)
        var_me = _metric_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, value=0.95)
        bl_ve = _validity_evidence(sha=bad_sha, source_run_id=self.RUN_ID, seed=42, status="valid")
        var_ve = _validity_evidence(sha=_SHA, source_run_id=self.RUN_ID, seed=42, status="valid")
        with pytest.raises(ValueError, match="sha256 mismatch"):
            validate_observation_against_metric_artifacts(obs, key, self.RUN_ID, _FP, bl_me, var_me, bl_ve, var_ve)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. validate_aggregate_from_observations
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateAggregateFromObservations:
    def test_valid_observations_recalculates_means(self):
        key = _agg_key()
        obs1 = _paired_obs(1, baseline_value=0.90, variant_value=0.95)
        obs2 = _paired_obs(2, baseline_value=0.91, variant_value=0.93)
        agg = AggregatedMetricComparison(
            aggregate_key=key,
            paired_observations=[obs1, obs2],
            comparison_status="valid",
            seed_count=2,
            completed_seed_count=2,
            mean_baseline=0.905,
            mean_variant=0.94,
            mean_raw_delta=0.035,
            mean_improvement_delta=0.035,
        )
        validate_aggregate_from_observations(agg)

    def test_means_mismatch_raises(self):
        key = _agg_key()
        obs1 = _paired_obs(1, baseline_value=0.90, variant_value=0.95)
        agg = AggregatedMetricComparison(
            aggregate_key=key,
            paired_observations=[obs1],
            comparison_status="valid",
            seed_count=1,
            completed_seed_count=1,
            mean_baseline=0.99,
            mean_variant=0.95,
            mean_raw_delta=0.05,
            mean_improvement_delta=0.05,
        )
        with pytest.raises(ValueError, match="mean_baseline mismatch"):
            validate_aggregate_from_observations(agg)

    def test_invalid_observations_require_degraded_status(self):
        key = _agg_key()
        obs = _paired_obs(1, baseline_value=0.90, variant_value=0.95)
        obs.pair_validity_status = "invalid"
        agg = AggregatedMetricComparison(
            aggregate_key=key,
            paired_observations=[obs],
            comparison_status="invalid",
            seed_count=1,
            completed_seed_count=0,
            mean_baseline=None,
            mean_variant=None,
            mean_raw_delta=None,
            mean_improvement_delta=None,
        )
        validate_aggregate_from_observations(agg)

    def test_invalid_observations_with_wrong_status_raises(self):
        key = _agg_key()
        obs = _paired_obs(1, baseline_value=0.90, variant_value=0.95)
        obs.pair_validity_status = "invalid"
        agg = AggregatedMetricComparison(
            aggregate_key=key,
            paired_observations=[obs],
            comparison_status="valid",
            seed_count=1,
            completed_seed_count=0,
            mean_baseline=None,
            mean_variant=None,
            mean_raw_delta=None,
            mean_improvement_delta=None,
        )
        with pytest.raises(ValueError, match="degraded.*missing.*invalid"):
            validate_aggregate_from_observations(agg)

    def test_no_valid_observations_means_none(self):
        key = _agg_key()
        agg = AggregatedMetricComparison(
            aggregate_key=key,
            paired_observations=[],
            comparison_status="missing",
            seed_count=0,
            completed_seed_count=0,
            mean_baseline=None,
            mean_variant=None,
            mean_raw_delta=None,
            mean_improvement_delta=None,
        )
        validate_aggregate_from_observations(agg)

    def test_no_valid_observations_means_not_none_raises(self):
        key = _agg_key()
        agg = AggregatedMetricComparison(
            aggregate_key=key,
            paired_observations=[],
            comparison_status="missing",
            seed_count=0,
            completed_seed_count=0,
            mean_baseline=0.5,
            mean_variant=None,
            mean_raw_delta=None,
            mean_improvement_delta=None,
        )
        with pytest.raises(ValueError, match="mean_baseline must be None"):
            validate_aggregate_from_observations(agg)

    def test_variant_id_mismatch_raises(self):
        key = _agg_key(variant_id="v1")
        obs = _paired_obs(1, variant_id="v2")
        agg = AggregatedMetricComparison(
            aggregate_key=key,
            paired_observations=[obs],
            comparison_status="valid",
            seed_count=1,
            completed_seed_count=1,
            mean_baseline=0.90,
            mean_variant=0.95,
            mean_raw_delta=0.05,
            mean_improvement_delta=0.05,
        )
        with pytest.raises(ValueError, match="variant_id"):
            validate_aggregate_from_observations(agg)

    def test_completed_seed_count_mismatch_raises(self):
        key = _agg_key()
        obs = _paired_obs(1, baseline_value=0.90, variant_value=0.95)
        agg = AggregatedMetricComparison(
            aggregate_key=key,
            paired_observations=[obs],
            comparison_status="valid",
            seed_count=1,
            completed_seed_count=2,
            mean_baseline=0.90,
            mean_variant=0.95,
            mean_raw_delta=0.05,
            mean_improvement_delta=0.05,
        )
        with pytest.raises(ValueError, match="completed_seed_count"):
            validate_aggregate_from_observations(agg)

    def test_direction_mismatch_raises(self):
        key = _agg_key(direction="minimize")
        obs = _paired_obs(1, baseline_value=0.90, variant_value=0.95, direction="maximize")
        agg = AggregatedMetricComparison(
            aggregate_key=key,
            paired_observations=[obs],
            comparison_status="valid",
            seed_count=1,
            completed_seed_count=1,
            mean_baseline=0.90,
            mean_variant=0.95,
            mean_raw_delta=0.05,
            mean_improvement_delta=0.05,
        )
        with pytest.raises(ValueError, match="direction"):
            validate_aggregate_from_observations(agg)


# ═══════════════════════════════════════════════════════════════════════════════
# 4b. aggregate mean None for valid obs
# ═══════════════════════════════════════════════════════════════════════════════


class TestAggregateMeanNoneForValidObs:
    def test_valid_obs_mean_none_raises(self):
        key = _agg_key()
        obs = _paired_obs(1, baseline_value=0.90, variant_value=0.95)
        agg = AggregatedMetricComparison(
            aggregate_key=key,
            paired_observations=[obs],
            comparison_status="valid",
            seed_count=1,
            completed_seed_count=1,
            mean_baseline=None,
            mean_variant=0.95,
            mean_raw_delta=0.05,
            mean_improvement_delta=0.05,
        )
        with pytest.raises(ValueError, match="mean_baseline required when valid observations exist"):
            validate_aggregate_from_observations(agg)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. derive_idea_support
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeriveIdeaSupport:
    SEEDS = [1, 2, 3]

    def test_consistently_supported(self):
        conclusions = [
            _scientific_conclusion("v1", ScientificConclusion.BENEFICIAL),
            _scientific_conclusion("v2", ScientificConclusion.BENEFICIAL),
        ]
        result = derive_idea_support(conclusions, ["v1", "v2"], "any", self.SEEDS)
        assert result == IdeaSupportConclusion.CONSISTENTLY_SUPPORTED

    def test_not_supported_by_tested(self):
        conclusions = [
            _scientific_conclusion("v1", ScientificConclusion.WORSE),
            _scientific_conclusion("v2", ScientificConclusion.WORSE),
        ]
        result = derive_idea_support(conclusions, ["v1", "v2"], "any", self.SEEDS)
        assert result == IdeaSupportConclusion.NOT_SUPPORTED_BY_TESTED

    def test_implementation_sensitive(self):
        conclusions = [
            _scientific_conclusion("v1", ScientificConclusion.BENEFICIAL),
            _scientific_conclusion("v2", ScientificConclusion.WORSE),
        ]
        result = derive_idea_support(conclusions, ["v1", "v2"], "any", self.SEEDS)
        assert result == IdeaSupportConclusion.IMPLEMENTATION_SENSITIVE

    def test_descriptive_only_multiple_variants(self):
        conclusions = [
            _scientific_conclusion("v1", ScientificConclusion.BENEFICIAL),
            _scientific_conclusion("v2", ScientificConclusion.BENEFICIAL),
        ]
        result = derive_idea_support(conclusions, ["v1", "v2"], "descriptive_only", self.SEEDS)
        assert result == IdeaSupportConclusion.MULTIPLE_VARIANTS_DESCRIPTIVE

    def test_duplicate_variant_ids_raises(self):
        conclusions = [
            _scientific_conclusion("v1", ScientificConclusion.BENEFICIAL),
        ]
        with pytest.raises(ValueError, match="duplicate"):
            derive_idea_support(conclusions, ["v1", "v1"], "any", self.SEEDS)

    def test_empty_selected_returns_cannot_judge(self):
        result = derive_idea_support([], [], "any", self.SEEDS)
        assert result == IdeaSupportConclusion.CANNOT_JUDGE

    def test_unselected_variant_conclusion_raises(self):
        conclusions = [
            _scientific_conclusion("v1", ScientificConclusion.BENEFICIAL),
            _scientific_conclusion("v2", ScientificConclusion.WORSE),
        ]
        with pytest.raises(ValueError, match="unselected"):
            derive_idea_support(conclusions, ["v1"], "any", self.SEEDS)

    def test_duplicate_conclusion_for_same_variant_raises(self):
        conclusions = [
            _scientific_conclusion("v1", ScientificConclusion.BENEFICIAL, matched_rule_id="a"),
            _scientific_conclusion("v1", ScientificConclusion.BENEFICIAL, matched_rule_id="b"),
        ]
        with pytest.raises(ValueError, match="duplicate conclusion"):
            derive_idea_support(conclusions, ["v1"], "any", self.SEEDS)

    def test_supported_by_at_least_one(self):
        conclusions = [
            _scientific_conclusion("v1", ScientificConclusion.BENEFICIAL),
            _scientific_conclusion("v2", ScientificConclusion.PRACTICALLY_EQUIVALENT),
        ]
        result = derive_idea_support(conclusions, ["v1", "v2"], "any", self.SEEDS)
        assert result == IdeaSupportConclusion.SUPPORTED_BY_AT_LEAST_ONE


# ═══════════════════════════════════════════════════════════════════════════════
# 6. determine_budget_assessment
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetermineBudgetAssessment:
    def test_within_budget(self):
        budget = _make_budget(max_per_experiment=10.0)
        agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        result = determine_budget_assessment("v1", budget, _ref("budget"), agg)
        assert result.status == "within_budget"

    def test_near_budget(self):
        budget = _make_budget(max_per_experiment=10.0)
        agg = _variant_agg("v1", per_unit={"unit_001": 9.0})
        result = determine_budget_assessment("v1", budget, _ref("budget"), agg)
        assert result.status == "near_budget"

    def test_exceeded_budget(self):
        budget = _make_budget(max_per_experiment=10.0)
        agg = _variant_agg("v1", per_unit={"unit_001": 15.0})
        result = determine_budget_assessment("v1", budget, _ref("budget"), agg)
        assert result.status == "exceeded_budget"

    def test_budget_none_not_assessable(self):
        agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        result = determine_budget_assessment("v1", None, None, agg)
        assert result.status == "not_assessable"
        assert "not found" in result.reason

    def test_ref_none_not_assessable(self):
        budget = _make_budget(max_per_experiment=10.0)
        agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        result = determine_budget_assessment("v1", budget, None, agg)
        assert result.status == "not_assessable"
        assert "missing" in result.reason

    def test_usage_none_not_assessable(self):
        budget = _make_budget(max_per_experiment=10.0)
        result = determine_budget_assessment("v1", budget, _ref("budget"), None)
        assert result.status == "not_assessable"

    def test_empty_per_unit_not_assessable(self):
        budget = _make_budget(max_per_experiment=10.0)
        agg = _variant_agg("v1", per_unit={})
        result = determine_budget_assessment("v1", budget, _ref("budget"), agg)
        assert result.status == "not_assessable"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. compare_per_experiment_usage
# ═══════════════════════════════════════════════════════════════════════════════


class TestComparePerExperimentUsage:
    def test_within_budget(self):
        budget = _make_budget(max_per_experiment=10.0)
        agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        assert compare_per_experiment_usage(agg, budget) == "within_budget"

    def test_near_budget(self):
        budget = _make_budget(max_per_experiment=10.0)
        agg = _variant_agg("v1", per_unit={"unit_001": 9.0})
        assert compare_per_experiment_usage(agg, budget) == "near_budget"

    def test_near_budget_boundary(self):
        budget = _make_budget(max_per_experiment=10.0)
        agg = _variant_agg("v1", per_unit={"unit_001": 8.999999})
        assert compare_per_experiment_usage(agg, budget) == "within_budget"

    def test_exceeded_budget(self):
        budget = _make_budget(max_per_experiment=10.0)
        agg = _variant_agg("v1", per_unit={"unit_001": 15.0})
        assert compare_per_experiment_usage(agg, budget) == "exceeded_budget"

    def test_zero_limit_zero_usage(self):
        budget = _make_budget(max_per_experiment=0.0)
        agg = _variant_agg("v1", per_unit={"unit_001": 0.0})
        assert compare_per_experiment_usage(agg, budget) == "within_budget"

    def test_zero_limit_nonzero_usage(self):
        budget = _make_budget(max_per_experiment=0.0)
        agg = _variant_agg("v1", per_unit={"unit_001": 1.0})
        assert compare_per_experiment_usage(agg, budget) == "exceeded_budget"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. determine_bundle_budget_assessment
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetermineBundleBudgetAssessment:
    def test_within_budget_with_full_coverage(self):
        budget = _make_budget(max_total=100.0)
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        result = determine_bundle_budget_assessment(
            bundle, budget, _ref("budget"),
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001"}},
        )
        assert result.status == "within_budget"

    def test_exceeded_bundle_total(self):
        budget = _make_budget(max_total=5.0)
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        result = determine_bundle_budget_assessment(
            bundle, budget, _ref("budget"),
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001"}},
        )
        assert result.status == "exceeded_budget"

    def test_near_bundle_total(self):
        budget = _make_budget(max_total=10.0)
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        result = determine_bundle_budget_assessment(
            bundle, budget, _ref("budget"),
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001"}},
        )
        assert result.status == "near_budget"

    def test_missing_baseline_unit_not_assessable(self):
        budget = _make_budget(max_total=100.0)
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        result = determine_bundle_budget_assessment(
            bundle, budget, _ref("budget"),
            expected_baseline_unit_ids={"unit_baseline", "unit_missing"},
            expected_variant_unit_ids={"v1": {"unit_001"}},
        )
        assert result.status == "not_assessable"
        assert "unit_missing" in result.missing_unit_ids

    def test_missing_variant_not_assessable(self):
        budget = _make_budget(max_total=100.0)
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        result = determine_bundle_budget_assessment(
            bundle, budget, _ref("budget"),
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001"}, "v2": {"unit_002"}},
        )
        assert result.status == "not_assessable"
        assert "v2" in result.missing_variant_ids

    def test_unexpected_baseline_unit_not_assessable(self):
        budget = _make_budget(max_total=100.0)
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0, "unit_extra": 1.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        result = determine_bundle_budget_assessment(
            bundle, budget, _ref("budget"),
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001"}},
        )
        assert result.status == "not_assessable"
        assert "unit_extra" in result.unexpected_unit_ids

    def test_missing_per_variant_unit_not_assessable(self):
        budget = _make_budget(max_total=100.0)
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        result = determine_bundle_budget_assessment(
            bundle, budget, _ref("budget"),
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001", "unit_002"}},
        )
        assert result.status == "not_assessable"
        assert "unit_002" in result.missing_unit_ids


# ═══════════════════════════════════════════════════════════════════════════════
# 9. validate_resource_comparison_report
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateResourceComparisonReport:
    def test_consistent_passes(self):
        budget = _make_budget(max_total=100.0, max_per_experiment=10.0)
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})

        delta = ResourceDelta(variant_id="v1", wall_time_delta_seconds=0.0, gpu_memory_delta_mb=0.0, measurement_compatible=True)
        ba = determine_budget_assessment("v1", budget, _ref("budget"), var_agg)
        bba = determine_bundle_budget_assessment(
            bundle, budget, _ref("budget"),
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001"}},
        )
        report = ResourceComparisonReport(
            baseline=bl_agg,
            per_variant={"v1": var_agg},
            per_variant_deltas={"v1": delta},
            per_variant_budget_assessments={"v1": ba},
            bundle=bundle,
            bundle_budget_assessment=bba,
        )
        resolved_budget = ResolvedArtifact(
            ref=_ref("budget"),
            verified_sha256=_SHA,
            payload=budget,
        )
        manifest = _exec_manifest(unit_records=[
            _unit_record("unit_baseline"),
            _unit_record("unit_001", variant_id="v1"),
        ])
        validate_resource_comparison_report(report, resolved_budget, manifest)

    def test_mismatch_raises(self):
        budget = _make_budget(max_total=100.0, max_per_experiment=10.0)
        var_agg = _variant_agg("v1", per_unit={"unit_001": 50.0})
        bundle = _bundle_agg(per_variant={"v1": var_agg})
        delta = ResourceDelta(variant_id="v1", measurement_compatible=True)
        ba = VariantBudgetAssessment(
            variant_id="v1",
            status="within_budget",
            reason="ok",
            resource_budget_ref=_ref("budget"),
            resource_usage_refs=[_ref("usage")],
        )
        wrong_bba = BundleBudgetAssessment(
            status="within_budget",
            max_unit_actual_gpu_hours=5.0,
            bundle_total_actual_gpu_hours=10.0,
            resource_budget_ref=_ref("budget"),
            resource_usage_refs=[_ref("usage")],
            reason="test",
        )
        report = ResourceComparisonReport(
            baseline=_baseline_agg(),
            per_variant={"v1": var_agg},
            per_variant_deltas={"v1": delta},
            per_variant_budget_assessments={"v1": ba},
            bundle=bundle,
            bundle_budget_assessment=wrong_bba,
        )
        resolved_budget = ResolvedArtifact(
            ref=_ref("budget"),
            verified_sha256=_SHA,
            payload=budget,
        )
        manifest = _exec_manifest(unit_records=[
            _unit_record("unit_baseline"),
            _unit_record("unit_001", variant_id="v1"),
        ])
        with pytest.raises(ValueError, match="does not match"):
            validate_resource_comparison_report(report, resolved_budget, manifest)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. validate_bundle_resource_coverage
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateBundleResourceCoverage:
    def test_full_coverage(self):
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        missing_u, unexpected_u, missing_v, unexpected_v = validate_bundle_resource_coverage(
            bundle,
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001"}},
        )
        assert missing_u == []
        assert unexpected_u == []
        assert missing_v == []
        assert unexpected_v == []

    def test_missing_unit_in_list(self):
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        missing_u, unexpected_u, missing_v, unexpected_v = validate_bundle_resource_coverage(
            bundle,
            expected_baseline_unit_ids={"unit_baseline", "unit_missing"},
            expected_variant_unit_ids={"v1": {"unit_001"}},
        )
        assert missing_u == ["unit_missing"]
        assert unexpected_u == []
        assert missing_v == []
        assert unexpected_v == []

    def test_unexpected_unit_in_list(self):
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0, "unit_extra": 1.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        missing_u, unexpected_u, missing_v, unexpected_v = validate_bundle_resource_coverage(
            bundle,
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001"}},
        )
        assert missing_u == []
        assert unexpected_u == ["unit_extra"]
        assert missing_v == []
        assert unexpected_v == []

    def test_missing_variant_in_list(self):
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        var_agg = _variant_agg("v1", per_unit={"unit_001": 5.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var_agg})
        missing_u, unexpected_u, missing_v, unexpected_v = validate_bundle_resource_coverage(
            bundle,
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001"}, "v2": {"unit_002"}},
        )
        assert missing_u == []
        assert unexpected_u == []
        assert missing_v == ["v2"]
        assert unexpected_v == []

    def test_unexpected_variant_in_list(self):
        bl_agg = _baseline_agg(per_unit={"unit_baseline": 4.0})
        var1 = _variant_agg("v1", per_unit={"unit_001": 5.0})
        var2 = _variant_agg("v2", per_unit={"unit_002": 3.0})
        bundle = _bundle_agg(baseline=bl_agg, per_variant={"v1": var1, "v2": var2})
        missing_u, unexpected_u, missing_v, unexpected_v = validate_bundle_resource_coverage(
            bundle,
            expected_baseline_unit_ids={"unit_baseline"},
            expected_variant_unit_ids={"v1": {"unit_001"}},
        )
        assert missing_u == []
        assert unexpected_u == []
        assert missing_v == []
        assert unexpected_v == ["v2"]
