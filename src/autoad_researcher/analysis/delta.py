"""Step 3.9: Delta computation between variant and baseline metrics."""

from autoad_researcher.schemas.results_analysis import (
    PairedMetricObservation,
    ResourceDelta,
    VariantResourceAggregate,
    BaselineResourceAggregate,
)


def compute_deltas(
    observations: list[PairedMetricObservation],
) -> list[PairedMetricObservation]:
    """Compute raw_delta and relative_delta_pct for each observation.

    Returns updated observations with computed delta fields filled in.
    """
    result: list[PairedMetricObservation] = []
    for obs in observations:
        if obs.variant_value is not None and obs.baseline_value is not None:
            raw_delta = obs.variant_value - obs.baseline_value
            if abs(obs.baseline_value) > 1e-12:
                relative_delta_pct = (raw_delta / obs.baseline_value) * 100.0
            else:
                relative_delta_pct = None
        else:
            raw_delta = None
            relative_delta_pct = None

        result.append(
            PairedMetricObservation(
                variant_metric_name=obs.variant_metric_name,
                variant_value=obs.variant_value,
                variant_parse_status=obs.variant_parse_status,
                variant_artifact_ref=obs.variant_artifact_ref,
                baseline_metric_name=obs.baseline_metric_name,
                baseline_value=obs.baseline_value,
                baseline_parse_status=obs.baseline_parse_status,
                baseline_artifact_ref=obs.baseline_artifact_ref,
                raw_delta=raw_delta,
                relative_delta_pct=relative_delta_pct,
                is_statistically_significant=obs.is_statistically_significant,
                p_value=obs.p_value,
            )
        )
    return result


def compute_resource_deltas(
    variant: VariantResourceAggregate,
    baseline: BaselineResourceAggregate,
) -> ResourceDelta:
    """Compute resource deltas between a variant and the baseline."""
    return ResourceDelta(
        variant_id=variant.variant_id,
        delta_gpu_hours=variant.gpu_hours - baseline.gpu_hours,
        delta_wall_time=variant.wall_time - baseline.wall_time,
    )
