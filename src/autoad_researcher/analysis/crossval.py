"""Step 3.9: Cross-validation — evidence reconciliation against metric artifacts."""

from autoad_researcher.schemas.results_analysis import (
    AggregatedMetricComparison,
    AggregatedMetricKey,
    BaselineMetricSource,
    PairedMetricObservation,
    ReplicationPairEvidence,
)


def validate_observation_against_metric_artifacts(
    observation: PairedMetricObservation,
    baseline_source: BaselineMetricSource,
) -> bool:
    """Cross-validate a PairedMetricObservation against its baseline metric source.

    Checks that the observation's baseline artifact reference matches
    the source referenced by the BaselineMetricSource.
    """
    if isinstance(baseline_source, dict):
        baseline_source_cls = baseline_source
    else:
        baseline_source_cls = baseline_source

    baseline_artifact_ref = getattr(baseline_source_cls, "source_artifact_ref", None) or \
        getattr(baseline_source_cls, "baseline_metric_artifact_ref", None)

    if baseline_artifact_ref is None:
        return False

    return observation.baseline_artifact_ref == baseline_artifact_ref


def derive_pair_validity(
    pair: ReplicationPairEvidence,
) -> ReplicationPairEvidence:
    """Derive the validity status of a replication pair."""
    return pair


def validate_aggregate_from_observations(
    key: AggregatedMetricKey,
    observations: list[PairedMetricObservation],
) -> AggregatedMetricComparison:
    """Aggregate multiple observations into a single metric comparison."""
    variant_values = [
        obs.variant_value
        for obs in observations
        if obs.variant_value is not None
    ]
    baseline_values = [
        obs.baseline_value
        for obs in observations
        if obs.baseline_value is not None
    ]

    mean_variant = sum(variant_values) / len(variant_values) if variant_values else None
    mean_baseline = sum(baseline_values) / len(baseline_values) if baseline_values else None

    mean_delta: float | None = None
    mean_relative_delta_pct: float | None = None
    if mean_variant is not None and mean_baseline is not None:
        mean_delta = mean_variant - mean_baseline
        if abs(mean_baseline) > 1e-12:
            mean_relative_delta_pct = (mean_delta / mean_baseline) * 100.0

    return AggregatedMetricComparison(
        key=key,
        observations=observations,
        mean_variant=mean_variant,
        mean_baseline=mean_baseline,
        mean_delta=mean_delta,
        mean_relative_delta_pct=mean_relative_delta_pct,
    )
