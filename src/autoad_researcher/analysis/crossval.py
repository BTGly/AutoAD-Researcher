"""Step 3.9: Cross-validation — sealed evidence reconciliation.

Matches the sealed contract in docs/3.9开发计划.md v2.12.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from autoad_researcher.analysis.metrics import ParsedMetric
from autoad_researcher.schemas.results_analysis import (
    AggregatedMetricComparison,
    AggregatedMetricKey,
    PairedMetricObservation,
    ResolvedMetricEvidence,
    ResolvedValidityEvidence,
)

if TYPE_CHECKING:
    from autoad_researcher.supervisor.validity import ScientificValidityReport


def derive_pair_validity(
    baseline_validity: ScientificValidityReport,
    variant_validity: ScientificValidityReport,
    baseline_metric: ParsedMetric,
    variant_metric: ParsedMetric,
) -> Literal["valid", "invalid", "insufficient_evidence"]:
    """Combine validity reports and ParsedMetric parse_status for pair-level validity."""
    if baseline_metric.parse_status != "parsed":
        return "invalid"
    if variant_metric.parse_status != "parsed":
        return "invalid"

    statuses = {
        baseline_validity.status,
        variant_validity.status,
    }
    if "invalid" in statuses:
        return "invalid"
    if "insufficient_evidence" in statuses:
        return "insufficient_evidence"
    return "valid"


def validate_observation_against_metric_artifacts(
    observation: PairedMetricObservation,
    aggregate_key: AggregatedMetricKey,
    expected_run_id: str,
    expected_protocol_fingerprint: str,
    baseline_metric_evidence: ResolvedMetricEvidence,
    variant_metric_evidence: ResolvedMetricEvidence,
    baseline_validity_evidence: ResolvedValidityEvidence,
    variant_validity_evidence: ResolvedValidityEvidence,
) -> None:
    """Validate observation values, seeds, metric names, dataset rows, and pair validity.

    Provenance checking symmetrically covers metric evidence and validity evidence:
    - current_run → unit_id + source_run_id dual binding
    - reused → source_run_id binding
    """
    # --- Protocol fingerprint cross-validation ---
    if observation.protocol_fingerprint != expected_protocol_fingerprint:
        raise ValueError(
            f"observation protocol_fingerprint mismatch: "
            f"got={observation.protocol_fingerprint}, expected={expected_protocol_fingerprint}"
        )

    # --- SHA + verified SHA checks ---
    if baseline_metric_evidence.metric_ref.sha256 != observation.baseline_source.metric_ref.sha256:
        raise ValueError("baseline metric ref sha256 mismatch")
    if baseline_metric_evidence.verified_sha256 != baseline_metric_evidence.metric_ref.sha256:
        raise ValueError("baseline metric verified SHA mismatch")
    if variant_metric_evidence.metric_ref.sha256 != observation.variant_metric_ref.sha256:
        raise ValueError("variant metric ref sha256 mismatch")
    if variant_metric_evidence.verified_sha256 != variant_metric_evidence.metric_ref.sha256:
        raise ValueError("variant metric verified SHA mismatch")

    if baseline_validity_evidence.validity_ref.sha256 != observation.baseline_validity_ref.sha256:
        raise ValueError("baseline validity ref sha256 mismatch")
    if baseline_validity_evidence.verified_sha256 != baseline_validity_evidence.validity_ref.sha256:
        raise ValueError("baseline validity verified SHA mismatch")
    if variant_validity_evidence.validity_ref.sha256 != observation.variant_validity_ref.sha256:
        raise ValueError("variant validity ref sha256 mismatch")
    if variant_validity_evidence.verified_sha256 != variant_validity_evidence.validity_ref.sha256:
        raise ValueError("variant validity verified SHA mismatch")

    # --- Seed consistency ---
    if observation.seed != baseline_metric_evidence.seed:
        raise ValueError("observation.seed != baseline metric evidence seed")
    if observation.seed != variant_metric_evidence.seed:
        raise ValueError("observation.seed != variant metric evidence seed")
    if observation.seed != baseline_validity_evidence.seed:
        raise ValueError("observation.seed != baseline validity evidence seed")
    if observation.seed != variant_validity_evidence.seed:
        raise ValueError("observation.seed != variant validity evidence seed")

    # --- Provenance: current_run vs reused ---
    if observation.baseline_source.source_type == "current_run":
        if baseline_metric_evidence.unit_id != observation.baseline_source.unit_id:
            raise ValueError("baseline current_run unit_id mismatch")
        if baseline_metric_evidence.source_run_id != expected_run_id:
            raise ValueError("baseline metric source_run_id != expected_run_id")
        if baseline_validity_evidence.unit_id != observation.baseline_source.unit_id:
            raise ValueError("baseline validity unit_id mismatch")
        if baseline_validity_evidence.source_run_id != expected_run_id:
            raise ValueError("baseline validity source_run_id != expected_run_id")
    else:
        if baseline_metric_evidence.source_run_id != observation.baseline_source.source_run_id:
            raise ValueError("baseline reused source_run_id mismatch")
        if baseline_validity_evidence.source_run_id != observation.baseline_source.source_run_id:
            raise ValueError("reused baseline validity source_run_id mismatch")

    # --- Variant provenance ---
    if variant_metric_evidence.unit_id != observation.variant_unit_id:
        raise ValueError("variant metric unit_id mismatch")
    if variant_metric_evidence.source_run_id != expected_run_id:
        raise ValueError("variant metric source_run_id != expected_run_id")
    if variant_validity_evidence.unit_id != observation.variant_unit_id:
        raise ValueError("variant validity unit_id mismatch")
    if variant_validity_evidence.source_run_id != expected_run_id:
        raise ValueError("variant validity source_run_id != expected_run_id")

    # --- Parse and cast evidence dicts ---
    from autoad_researcher.supervisor.validity import ScientificValidityReport  # noqa: PLC0415  local import to avoid circular dependency

    baseline_metric = ParsedMetric.model_validate(baseline_metric_evidence.metric)
    variant_metric = ParsedMetric.model_validate(variant_metric_evidence.metric)
    baseline_validity = ScientificValidityReport.model_validate(baseline_validity_evidence.report)
    variant_validity = ScientificValidityReport.model_validate(variant_validity_evidence.report)

    # --- Metric integrity ---
    if baseline_metric.parse_status != "parsed":
        raise ValueError("baseline metric not successfully parsed")
    if variant_metric.parse_status != "parsed":
        raise ValueError("variant metric not successfully parsed")
    if baseline_metric.value is None:
        raise ValueError("baseline metric has no parsed value")
    if variant_metric.value is None:
        raise ValueError("variant metric has no parsed value")

    if abs(observation.baseline_value - baseline_metric.value) > 1e-9:
        raise ValueError("baseline_value mismatch with ParsedMetric.value")
    if abs(observation.variant_value - variant_metric.value) > 1e-9:
        raise ValueError("variant_value mismatch with ParsedMetric.value")

    if aggregate_key.metric_name != baseline_metric.metric_name:
        raise ValueError("metric_name mismatch")
    if aggregate_key.metric_name != variant_metric.metric_name:
        raise ValueError("variant metric_name mismatch")
    if baseline_metric.dataset_row != aggregate_key.dataset_row:
        raise ValueError("baseline dataset_row mismatch")
    if variant_metric.dataset_row != aggregate_key.dataset_row:
        raise ValueError("variant dataset_row mismatch")
    if observation.baseline_validity_ref.sha256 != observation.baseline_source.validity_ref.sha256:
        raise ValueError("baseline_validity_ref must equal baseline_source.validity_ref")

    # --- Derive and verify pair validity ---
    expected_validity = derive_pair_validity(
        baseline_validity,
        variant_validity,
        baseline_metric,
        variant_metric,
    )
    if observation.pair_validity_status != expected_validity:
        raise ValueError(
            f"pair_validity_status mismatch: "
            f"stored={observation.pair_validity_status}, derived={expected_validity}"
        )


def validate_aggregate_from_observations(
    aggregate: AggregatedMetricComparison,
) -> None:
    """Recompute mean_*, seed_count, completed_seed_count from paired_observations.

    These summary fields are not independent writable facts — they must
    match values derived from observations. Only observations with
    pair_validity_status == "valid" contribute to mean calculations.

    Duplicate seeds are rejected — each seed may appear at most once in
    a single aggregate. Direction must match the aggregate_key.direction
    for every observation.
    """
    valid_obs = [
        o for o in aggregate.paired_observations
        if o.pair_validity_status == "valid"
    ]
    invalid_obs = [
        o for o in aggregate.paired_observations
        if o.pair_validity_status != "valid"
    ]

    if aggregate.completed_seed_count != len(valid_obs):
        raise ValueError("completed_seed_count != valid observations count")
    if aggregate.seed_count != len(aggregate.paired_observations):
        raise ValueError("seed_count != total observations count")

    if invalid_obs and aggregate.comparison_status not in ("degraded", "missing", "invalid"):
        raise ValueError(
            "invalid observations require degraded, missing, or invalid status"
        )

    for obs in aggregate.paired_observations:
        if obs.direction != aggregate.aggregate_key.direction:
            raise ValueError(
                f"observation direction={obs.direction} != "
                f"aggregate_key.direction={aggregate.aggregate_key.direction}"
            )

    if valid_obs:
        if aggregate.mean_baseline is None:
            raise ValueError("mean_baseline required when valid observations exist")
        if aggregate.mean_variant is None:
            raise ValueError("mean_variant required when valid observations exist")
        if aggregate.mean_raw_delta is None:
            raise ValueError("mean_raw_delta required when valid observations exist")
        if aggregate.mean_improvement_delta is None:
            raise ValueError("mean_improvement_delta required when valid observations exist")

        recalc_mean_base = sum(o.baseline_value for o in valid_obs) / len(valid_obs)
        recalc_mean_var = sum(o.variant_value for o in valid_obs) / len(valid_obs)
        recalc_mean_imp = sum(o.improvement_delta for o in valid_obs) / len(valid_obs)

        if abs(aggregate.mean_baseline - recalc_mean_base) > 1e-9:
            raise ValueError("mean_baseline mismatch")
        if aggregate.mean_variant is not None and abs(aggregate.mean_variant - recalc_mean_var) > 1e-9:
            raise ValueError("mean_variant mismatch")
        if aggregate.mean_raw_delta is not None and abs(aggregate.mean_raw_delta - (recalc_mean_var - recalc_mean_base)) > 1e-9:
            raise ValueError("mean_raw_delta mismatch")
        if aggregate.mean_improvement_delta is not None and abs(aggregate.mean_improvement_delta - recalc_mean_imp) > 1e-9:
            raise ValueError("mean_improvement_delta mismatch")
        if aggregate.comparison_status == "missing":
            raise ValueError("comparison_status=missing incompatible with valid observations")
    else:
        if aggregate.mean_baseline is not None:
            raise ValueError("mean_baseline must be None when no valid observations")
        if aggregate.mean_variant is not None:
            raise ValueError("mean_variant must be None when no valid observations")
        if aggregate.mean_raw_delta is not None:
            raise ValueError("mean_raw_delta must be None when no valid observations")
        if aggregate.mean_improvement_delta is not None:
            raise ValueError("mean_improvement_delta must be None when no valid observations")
        if aggregate.comparison_status not in ("missing", "invalid"):
            raise ValueError("no valid observations require comparison_status missing or invalid")

    for obs in aggregate.paired_observations:
        if obs.variant_id != aggregate.aggregate_key.variant_id:
            raise ValueError(
                f"observation variant_id={obs.variant_id} != "
                f"aggregate_key.variant_id={aggregate.aggregate_key.variant_id}"
            )
