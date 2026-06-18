"""Metrics analysis and report helpers."""

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
    derive_pair_validity,
    validate_aggregate_from_observations,
    validate_observation_against_metric_artifacts,
)
from autoad_researcher.analysis.delta import (
    compute_deltas,
)
from autoad_researcher.analysis.metrics import (
    MetricParseSpec,
    MetricsReport,
    ParsedMetric,
    parse_metrics,
)
from autoad_researcher.analysis.reproducibility import (
    AttemptEvidenceSummary,
    InvariantCheck,
    MetricComparison,
    ReproducibilityReport,
    compare_attempts,
)

__all__ = [
    "AttemptEvidenceSummary",
    "InvariantCheck",
    "MetricParseSpec",
    "MetricComparison",
    "MetricsReport",
    "ParsedMetric",
    "ReproducibilityReport",
    "compare_attempts",
    "parse_metrics",
    # Step 3.9 analysis
    "compare_per_experiment_usage",
    "compute_deltas",
    "derive_idea_support",
    "derive_pair_validity",
    "determine_budget_assessment",
    "determine_bundle_budget_assessment",
    "validate_aggregate_from_observations",
    "validate_bundle_resource_coverage",
    "validate_observation_against_metric_artifacts",
    "validate_resource_comparison_report",
]
