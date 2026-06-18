"""Step 3.9: Results Analysis — sealed schemas.

All models in this module are sealed and should not be modified without
updating the 3.9 contract boundary.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from autoad_researcher.paper_intelligence.ids import IdentifierPattern
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2

# ── Enums ─────────────────────────────────────────────────────────────────────

IdeaSupportConclusion = Literal[
    "supported",
    "partially_supported",
    "not_supported",
    "inconclusive",
]

NextAction = Literal[
    "conclude_and_report",
    "refine_and_retry",
    "design_new_variant",
    "escalate_to_user",
]

# ── Metrics / Baseline comparison ─────────────────────────────────────────────


class CurrentRunBaselineMetricRef(BaseModel):
    """Reference to a baseline metric produced within the same run."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str = Field(min_length=1)
    baseline_metric_artifact_ref: ArtifactReferenceV2
    current_metric_name_in_run: str = Field(min_length=1)
    current_artifact_ref: ArtifactReferenceV2
    aggregation_method: str = Field(min_length=1)


class ReusedBaselineMetricRef(BaseModel):
    """Reference to a baseline metric reused from a prior run."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str = Field(min_length=1)
    source_run_id: str = Field(pattern=IdentifierPattern)
    source_artifact_ref: ArtifactReferenceV2


BaselineMetricSource = CurrentRunBaselineMetricRef | ReusedBaselineMetricRef


class PairedMetricObservation(BaseModel):
    """One paired observation: a variant metric vs its baseline counterpart."""

    model_config = ConfigDict(extra="forbid")

    variant_metric_name: str = Field(min_length=1)
    variant_value: float | None = None
    variant_parse_status: str = Field(min_length=1)
    variant_artifact_ref: ArtifactReferenceV2
    baseline_metric_name: str = Field(min_length=1)
    baseline_value: float | None = None
    baseline_parse_status: str = Field(min_length=1)
    baseline_artifact_ref: ArtifactReferenceV2
    raw_delta: float | None = None
    relative_delta_pct: float | None = None
    is_statistically_significant: bool = False
    p_value: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _deltas_recomputed(self) -> "PairedMetricObservation":
        if self.variant_value is not None and self.baseline_value is not None:
            expected_raw = self.variant_value - self.baseline_value
            if self.raw_delta is not None and abs(self.raw_delta - expected_raw) > 1e-12:
                raise ValueError(
                    f"raw_delta ({self.raw_delta}) != variant - baseline ({expected_raw})"
                )
        return self


class MetricObservationKey(BaseModel):
    """Key identifying a specific observation within the execution matrix."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(pattern=IdentifierPattern)
    attempt_number: int = Field(ge=1)
    role: str = Field(min_length=1)


class AggregatedMetricKey(BaseModel):
    """Key for an aggregated metric across multiple observations."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str = Field(min_length=1)
    dataset_row: str = Field(min_length=1)
    unit: str = Field(min_length=1)


class AggregatedMetricComparison(BaseModel):
    """Aggregated comparison of a single metric across variant and baseline."""

    model_config = ConfigDict(extra="forbid")

    key: AggregatedMetricKey
    observations: list[PairedMetricObservation] = Field(default_factory=list)
    mean_variant: float | None = None
    mean_baseline: float | None = None
    mean_delta: float | None = None
    mean_relative_delta_pct: float | None = None
    standard_error: float | None = Field(default=None, ge=0)
    ci95_lower: float | None = None
    ci95_upper: float | None = None
    effect_size_cohens_d: float | None = None


class ResolvedMetricEvidence(BaseModel):
    """All resolved metric evidence for a variant analysis."""

    model_config = ConfigDict(extra="forbid")

    metric_comparisons: list[AggregatedMetricComparison] = Field(default_factory=list)
    baseline_metric_source: BaselineMetricSource | None = None


class ResolvedValidityEvidence(BaseModel):
    """Aggregated validity evidence across variant and baseline attempts."""

    model_config = ConfigDict(extra="forbid")

    validity_reports: list[ArtifactReferenceV2] = Field(default_factory=list)
    baseline_validity_refs: list[ArtifactReferenceV2] = Field(default_factory=list)
    overall_valid: bool = False


# ── Evidence / Conclusions ────────────────────────────────────────────────────


class EvidenceSufficiency(BaseModel):
    """Assessment of whether sufficient evidence exists to draw a conclusion."""

    model_config = ConfigDict(extra="forbid")

    all_metrics_accounted_for: bool = False
    all_validity_checks_passed: bool = False
    sufficient_seeds_available: bool = False
    sufficiency_summary: str = Field(min_length=1)


class VariantScientificConclusion(BaseModel):
    """Scientific conclusion for one variant based on all available evidence."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    evidence: ResolvedMetricEvidence | None = None
    sufficiency: EvidenceSufficiency | None = None
    conclusion: IdeaSupportConclusion
    confidence: float = Field(ge=0, le=1)
    supporting_metrics: list[str] = Field(default_factory=list)
    contradicting_metrics: list[str] = Field(default_factory=list)


# ── Reproducibility ───────────────────────────────────────────────────────────


class ReplicationPairEvidence(BaseModel):
    """Evidence from comparing one variant attempt to one baseline attempt."""

    model_config = ConfigDict(extra="forbid")

    pair_id: str = Field(pattern=IdentifierPattern)
    variant_attempt_ref: ArtifactReferenceV2
    baseline_attempt_ref: ArtifactReferenceV2
    metric_comparisons: list = Field(default_factory=list)
    status: Literal["reproducible", "not_reproducible", "insufficient_evidence"]


class ReplicationGroup(BaseModel):
    """All replication pairs for one variant."""

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(pattern=IdentifierPattern)
    variant_id: str = Field(pattern=IdentifierPattern)
    pairs: list[ReplicationPairEvidence] = Field(default_factory=list)
    overall_status: Literal["reproducible", "not_reproducible", "insufficient_evidence"]


class ReproducibilityInterpretation(BaseModel):
    """Overall reproducibility interpretation across all variants."""

    model_config = ConfigDict(extra="forbid")

    groups: list[ReplicationGroup] = Field(default_factory=list)
    overall_reproducible: bool = False


# ── Validity ──────────────────────────────────────────────────────────────────


class ValidityInterpretation(BaseModel):
    """Overall validity interpretation across variant and baseline."""

    model_config = ConfigDict(extra="forbid")

    variant_validity_reports: list[ArtifactReferenceV2] = Field(default_factory=list)
    baseline_validity_reports: list[ArtifactReferenceV2] = Field(default_factory=list)
    overall_valid: bool = False


# ── Resource / Budget ─────────────────────────────────────────────────────────


class VariantResourceAggregate(BaseModel):
    """Aggregated resource consumption for one variant."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    total_attempts: int = Field(ge=0)
    gpu_hours: float = Field(ge=0)
    wall_time: float = Field(ge=0)

    @computed_field
    @property
    def total_actual_gpu_hours(self) -> float:
        return self.gpu_hours

    @model_validator(mode="after")
    def _validate_gpu_hours_non_negative(self) -> "VariantResourceAggregate":
        if self.gpu_hours < 0:
            raise ValueError(f"gpu_hours must be >= 0, got {self.gpu_hours}")
        if self.wall_time < 0:
            raise ValueError(f"wall_time must be >= 0, got {self.wall_time}")
        return self


class BaselineResourceAggregate(BaseModel):
    """Aggregated resource consumption for the baseline."""

    model_config = ConfigDict(extra="forbid")

    total_attempts: int = Field(ge=0)
    gpu_hours: float = Field(ge=0)
    wall_time: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_gpu_hours_non_negative(self) -> "BaselineResourceAggregate":
        if self.gpu_hours < 0:
            raise ValueError(f"gpu_hours must be >= 0, got {self.gpu_hours}")
        if self.wall_time < 0:
            raise ValueError(f"wall_time must be >= 0, got {self.wall_time}")
        return self


class ResourceDelta(BaseModel):
    """Delta between variant and baseline resource consumption."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    delta_gpu_hours: float
    delta_wall_time: float


class VariantBudgetAssessment(BaseModel):
    """Budget assessment for a single variant."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    resource_aggregate: VariantResourceAggregate
    budget_remaining: float = Field(ge=0)
    within_budget: bool
    reason: str = Field(min_length=1)


class ResourceComparisonReport(BaseModel):
    """Complete resource comparison across all variants and baseline."""

    model_config = ConfigDict(extra="forbid")

    variant_aggregates: list[VariantResourceAggregate] = Field(default_factory=list)
    baseline_aggregate: BaselineResourceAggregate | None = None
    deltas: list[ResourceDelta] = Field(default_factory=list)
    per_variant_assessments: list[VariantBudgetAssessment] = Field(default_factory=list)
    overall_within_budget: bool = False


class BundleResourceAggregate(BaseModel):
    """Aggregated resource consumption for an experiment bundle."""

    model_config = ConfigDict(extra="forbid")

    bundle_id: str = Field(pattern=IdentifierPattern)
    resource_reports: list[ArtifactReferenceV2] = Field(default_factory=list)
    total_gpu_hours: float = Field(ge=0)
    total_wall_time: float = Field(ge=0)


class BundleBudgetAssessment(BaseModel):
    """Budget assessment for an experiment bundle."""

    model_config = ConfigDict(extra="forbid")

    bundle_id: str = Field(pattern=IdentifierPattern)
    bundle_aggregate: BundleResourceAggregate
    budget_ref: ArtifactReferenceV2
    within_budget: bool
    reason: str = Field(min_length=1)


# ── Failure Analysis ──────────────────────────────────────────────────────────


class FailureAnalysis(BaseModel):
    """Analysis of execution failures across a run."""

    model_config = ConfigDict(extra="forbid")

    unit_records: list = Field(default_factory=list)
    failure_summary: str = Field(min_length=1)
    terminal_units: list[str] = Field(default_factory=list)
    retry_patterns: list = Field(default_factory=list)


# ── Reflection ────────────────────────────────────────────────────────────────


class NextRunProposal(BaseModel):
    """Proposed next action based on the current run's results."""

    model_config = ConfigDict(extra="forbid")

    proposed_next_action: NextAction
    rationale: str = Field(min_length=1)
    suggested_modifications: list[str] = Field(default_factory=list)
    estimated_impact: str = Field(min_length=1)


class ReportFacts(BaseModel):
    """Summary facts about the run for reporting."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=IdentifierPattern)
    num_variants: int = Field(ge=0)
    num_successful: int = Field(ge=0)
    num_failed: int = Field(ge=0)
    total_gpu_hours: float = Field(ge=0)
    total_wall_time_seconds: float = Field(ge=0)


class Reflection(BaseModel):
    """Top-level reflection on a completed run."""

    model_config = ConfigDict(extra="forbid")

    per_variant_conclusions: list[VariantScientificConclusion] = Field(
        default_factory=list
    )
    resource_report: ResourceComparisonReport | None = None
    failure_analysis: FailureAnalysis | None = None
    next_run_proposal: NextRunProposal | None = None
    report_facts: ReportFacts | None = None
