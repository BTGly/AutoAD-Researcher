"""Step 3.9: Results Analysis — sealed schemas v2.12.

All models match the sealed contract in docs/3.9开发计划.md.
"""

import math
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.experiment_planning import ScientificConclusion

# ── Enums ─────────────────────────────────────────────────────────────────────


class IdeaSupportConclusion(str, Enum):
    MULTIPLE_VARIANTS_DESCRIPTIVE = "multiple_variants_descriptive"
    IMPLEMENTATION_SENSITIVE = "implementation_sensitive"
    CONSISTENTLY_SUPPORTED = "consistently_supported"
    SUPPORTED_BY_AT_LEAST_ONE = "supported_by_at_least_one_variant"
    NOT_SUPPORTED_BY_TESTED = "not_supported_by_tested_variants"
    NOT_SUPPORTED_OR_NOT_DEMONSTRATED = "not_supported_or_not_demonstrated"
    NOT_DEMONSTRATED_OR_PARTIAL = "not_demonstrated_or_partial"
    CANNOT_JUDGE = "cannot_judge"


# ── Baseline metric source ────────────────────────────────────────────────────


class CurrentRunBaselineMetricRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["current_run"] = "current_run"
    metric_name: str = Field(min_length=1)
    unit_id: str = Field(pattern=IdentifierPattern)
    seed: int
    metric_ref: ArtifactReferenceV2
    validity_ref: ArtifactReferenceV2


class ReusedBaselineMetricRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["reused"] = "reused"
    metric_name: str = Field(min_length=1)
    source_run_id: str = Field(pattern=IdentifierPattern)
    seed: int
    metric_ref: ArtifactReferenceV2
    validity_ref: ArtifactReferenceV2


BaselineMetricSource = CurrentRunBaselineMetricRef | ReusedBaselineMetricRef


# ── Paired observation ────────────────────────────────────────────────────────


class PairedMetricObservation(BaseModel):
    """One paired observation: variant metric vs its baseline counterpart.

    The _deltas_recomputed validator ensures raw_delta, improvement_delta,
    and relative change percentages are derived correctly from the raw values
    and direction, not injected independently.
    """

    model_config = ConfigDict(extra="forbid")

    seed: int
    baseline_source: BaselineMetricSource
    baseline_value: float
    variant_unit_id: str = Field(pattern=IdentifierPattern)
    variant_id: str = Field(pattern=IdentifierPattern)
    variant_metric_ref: ArtifactReferenceV2
    variant_value: float
    direction: Literal["maximize", "minimize"]
    raw_delta: float
    improvement_delta: float
    raw_relative_change_pct: float | None = None
    improvement_relative_change_pct: float | None = None
    pair_validity_status: Literal["valid", "invalid", "insufficient_evidence"]
    variant_validity_ref: ArtifactReferenceV2
    baseline_validity_ref: ArtifactReferenceV2
    protocol_fingerprint: str = Field(min_length=1)

    @model_validator(mode="after")
    def _deltas_recomputed(self) -> "PairedMetricObservation":
        recalc_raw = self.variant_value - self.baseline_value
        if self.direction == "maximize":
            recalc_imp = recalc_raw
        else:
            recalc_imp = self.baseline_value - self.variant_value
        abs_base = abs(self.baseline_value)
        _EPSILON = 1e-10
        if abs_base < _EPSILON:
            recalc_raw_pct = None
            recalc_imp_pct = None
        else:
            recalc_raw_pct = recalc_raw / abs_base * 100.0
            recalc_imp_pct = recalc_imp / abs_base * 100.0

        if abs(self.raw_delta - recalc_raw) > 1e-9:
            raise ValueError("raw_delta mismatch")
        if abs(self.improvement_delta - recalc_imp) > 1e-9:
            raise ValueError("improvement_delta mismatch")
        if recalc_raw_pct is None:
            if self.raw_relative_change_pct is not None:
                raise ValueError("raw_relative_change_pct should be None (baseline≈0)")
        elif self.raw_relative_change_pct is None:
            raise ValueError("raw_relative_change_pct should not be None")
        elif abs(self.raw_relative_change_pct - recalc_raw_pct) > 1e-9:
            raise ValueError("raw_relative_change_pct mismatch")
        if recalc_imp_pct is None:
            if self.improvement_relative_change_pct is not None:
                raise ValueError("improvement_relative_change_pct should be None (baseline≈0)")
        elif self.improvement_relative_change_pct is None:
            raise ValueError("improvement_relative_change_pct should not be None")
        elif abs(self.improvement_relative_change_pct - recalc_imp_pct) > 1e-9:
            raise ValueError("improvement_relative_change_pct mismatch")
        return self


# ── Keys ──────────────────────────────────────────────────────────────────────


class MetricObservationKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str = Field(pattern=IdentifierPattern)
    seed: int
    role: str = Field(min_length=1)


class AggregatedMetricKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant_id: str = Field(pattern=IdentifierPattern)
    metric_name: str = Field(min_length=1)
    dataset_row: str = Field(min_length=1)
    direction: Literal["maximize", "minimize"]


# ── Aggregated comparison ─────────────────────────────────────────────────────


class AggregatedMetricComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregate_key: AggregatedMetricKey
    paired_observations: list[PairedMetricObservation] = Field(default_factory=list)
    comparison_status: Literal["missing", "invalid", "degraded", "valid", "practically_equivalent"]
    seed_count: int = Field(ge=0)
    completed_seed_count: int = Field(ge=0)
    mean_baseline: float | None = None
    mean_variant: float | None = None
    mean_raw_delta: float | None = None
    mean_improvement_delta: float | None = None


# ── Evidence ──────────────────────────────────────────────────────────────────


class ResolvedMetricEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_ref: ArtifactReferenceV2
    verified_sha256: str = Field(pattern=Sha256Pattern)
    source_run_id: str = Field(pattern=IdentifierPattern)
    unit_id: str | None = None
    seed: int
    metric: dict  # ParsedMetric — imported at runtime to avoid circular dependency


class ResolvedValidityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    validity_ref: ArtifactReferenceV2
    verified_sha256: str = Field(pattern=Sha256Pattern)
    source_run_id: str = Field(pattern=IdentifierPattern)
    unit_id: str | None = None
    seed: int
    report: dict  # ScientificValidityReport — imported at runtime


# ── Sufficiency ───────────────────────────────────────────────────────────────


class EvidenceSufficiency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    total_planned_seeds: int = Field(ge=0)
    completed_seed_pairs: int = Field(ge=0)
    valid_seed_pairs: int = Field(ge=0)
    metric_count: int = Field(ge=0)
    valid_metric_count: int = Field(ge=0)
    protocol_fingerprint: str = Field(min_length=1)
    evidence_refs: list[ArtifactReferenceV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def _quantity_relations(self) -> "EvidenceSufficiency":
        if not (0 <= self.valid_seed_pairs <= self.completed_seed_pairs <= self.total_planned_seeds):
            raise ValueError(
                "require 0 <= valid_seed_pairs <= completed_seed_pairs <= total_planned_seeds"
            )
        if not (0 <= self.valid_metric_count <= self.metric_count):
            raise ValueError("require 0 <= valid_metric_count <= metric_count")
        return self


# ── Variant scientific conclusion ─────────────────────────────────────────────


class VariantScientificConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    conclusion: ScientificConclusion
    matched_rule_id: str
    completed_seed_pairs: list[int] = Field(default_factory=list)
    missing_seed_pairs: list[int] = Field(default_factory=list)
    evidence_refs: list[ArtifactReferenceV2] = Field(default_factory=list)


# ── Reproducibility ───────────────────────────────────────────────────────────


class ReplicationPairEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pair_id: str = Field(pattern=IdentifierPattern)
    seed: int
    paired_observation_ref: ArtifactReferenceV2
    improvement_delta: float
    validity_status: Literal["valid", "invalid", "insufficient_evidence"]


class ReplicationGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str = Field(pattern=IdentifierPattern)
    variant_id: str = Field(pattern=IdentifierPattern)
    pairs: list[ReplicationPairEvidence] = Field(default_factory=list)
    overall_status: Literal["reproducible", "not_reproducible", "insufficient_evidence"]


class ReproducibilityInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[ReplicationGroup] = Field(default_factory=list)
    overall_reproducible: bool = False


# ── Validity interpretation ───────────────────────────────────────────────────


class ValidityInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant_validity_reports: list[ArtifactReferenceV2] = Field(default_factory=list)
    baseline_validity_reports: list[ArtifactReferenceV2] = Field(default_factory=list)
    overall_valid: bool = False


# ── Resource aggregates ───────────────────────────────────────────────────────


class VariantResourceAggregate(BaseModel):
    """Per-variant aggregated resource consumption across attempts.

    per_unit_actual_gpu_hours retains unit-level granularity, enabling
    budget comparison to distinguish "max per experiment" from "total" caps.
    """

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    attempt_report_refs: list[ArtifactReferenceV2] = Field(default_factory=list)
    per_unit_actual_gpu_hours: dict[str, float] = Field(default_factory=dict)
    total_wall_time_seconds: float | None = None
    peak_gpu_memory_mb: float | None = None
    measurement_status: Literal["measured", "partially_measured", "not_available"]

    @computed_field
    @property
    def total_actual_gpu_hours(self) -> float:
        return sum(self.per_unit_actual_gpu_hours.values())

    @model_validator(mode="after")
    def _validate_gpu_hours(self) -> "VariantResourceAggregate":
        for unit_id, value in self.per_unit_actual_gpu_hours.items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid GPU-hours for unit {unit_id}: {value}")
        return self


class BaselineResourceAggregate(BaseModel):
    """Per-baseline aggregated resource consumption across attempts."""

    model_config = ConfigDict(extra="forbid")

    attempt_report_refs: list[ArtifactReferenceV2] = Field(default_factory=list)
    per_unit_actual_gpu_hours: dict[str, float] = Field(default_factory=dict)
    total_wall_time_seconds: float | None = None
    peak_gpu_memory_mb: float | None = None
    measurement_status: Literal["measured", "partially_measured", "not_available"]

    @computed_field
    @property
    def total_actual_gpu_hours(self) -> float:
        return sum(self.per_unit_actual_gpu_hours.values())

    @model_validator(mode="after")
    def _validate_gpu_hours(self) -> "BaselineResourceAggregate":
        for unit_id, value in self.per_unit_actual_gpu_hours.items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid GPU-hours for unit {unit_id}: {value}")
        return self


class ResourceDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant_id: str = Field(pattern=IdentifierPattern)
    wall_time_delta_seconds: float | None = None
    gpu_memory_delta_mb: float | None = None
    measurement_compatible: bool


# ── Budget assessments ────────────────────────────────────────────────────────


class VariantBudgetAssessment(BaseModel):
    """Per-variant structured budget assessment.

    Missing telemetry or unavailable budget artifact must be marked not_assessable.
    Default 0 or partial-field inference of "within budget" is forbidden.
    """

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    status: Literal["within_budget", "near_budget", "exceeded_budget", "not_assessable"]
    reason: str = Field(min_length=1)
    resource_budget_ref: ArtifactReferenceV2 | None = None
    resource_usage_refs: list[ArtifactReferenceV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "VariantBudgetAssessment":
        if self.status in ("within_budget", "near_budget", "exceeded_budget"):
            if self.resource_budget_ref is None:
                raise ValueError(f"status={self.status} requires resource_budget_ref")
            if not self.resource_usage_refs:
                raise ValueError(f"status={self.status} requires resource_usage_refs")
        return self


class BundleResourceAggregate(BaseModel):
    """Bundle-level aggregation of baseline + all variant resources."""

    model_config = ConfigDict(extra="forbid")

    baseline: BaselineResourceAggregate
    per_variant: dict[str, VariantResourceAggregate] = Field(default_factory=dict)

    @computed_field
    @property
    def total_actual_gpu_hours(self) -> float:
        return (
            self.baseline.total_actual_gpu_hours
            + sum(v.total_actual_gpu_hours for v in self.per_variant.values())
        )

    @computed_field
    @property
    def max_unit_actual_gpu_hours(self) -> float:
        values = list(self.baseline.per_unit_actual_gpu_hours.values())
        for v in self.per_variant.values():
            values.extend(v.per_unit_actual_gpu_hours.values())
        return max(values, default=0.0)


class BundleBudgetAssessment(BaseModel):
    """Bundle-level structured budget assessment.

    Complements VariantBudgetAssessment (per-experiment cap only).
    - VariantBudgetAssessment: max unit GPU-hours vs max_per_experiment_gpu_hours
    - BundleBudgetAssessment: total bundle GPU-hours vs max_total_gpu_hours

    Coverage is exact-set validation (missing + unexpected). Incomplete
    coverage forces status=not_assessable.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["within_budget", "near_budget", "exceeded_budget", "not_assessable"]
    max_unit_actual_gpu_hours: float | None = None
    bundle_total_actual_gpu_hours: float | None = None
    resource_budget_ref: ArtifactReferenceV2 | None = None
    resource_usage_refs: list[ArtifactReferenceV2] = Field(default_factory=list)
    missing_unit_ids: list[str] = Field(default_factory=list)
    unexpected_unit_ids: list[str] = Field(default_factory=list)
    missing_variant_ids: list[str] = Field(default_factory=list)
    unexpected_variant_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate(self) -> "BundleBudgetAssessment":
        has_coverage_issue = bool(
            self.missing_unit_ids
            or self.unexpected_unit_ids
            or self.missing_variant_ids
            or self.unexpected_variant_ids
        )
        if has_coverage_issue:
            if self.status != "not_assessable":
                raise ValueError(
                    "coverage mismatch (missing/unexpected unit/variant) "
                    "requires status=not_assessable"
                )
        if self.status in ("within_budget", "near_budget", "exceeded_budget"):
            if self.resource_budget_ref is None:
                raise ValueError(f"status={self.status} requires resource_budget_ref")
            if not self.resource_usage_refs:
                raise ValueError(f"status={self.status} requires resource_usage_refs")
            if self.max_unit_actual_gpu_hours is None:
                raise ValueError(f"status={self.status} requires max_unit_actual_gpu_hours")
            if self.bundle_total_actual_gpu_hours is None:
                raise ValueError(f"status={self.status} requires bundle_total_actual_gpu_hours")
            if has_coverage_issue:
                raise ValueError(f"status={self.status} requires empty coverage mismatch lists")
        return self


# ── Resource comparison report ────────────────────────────────────────────────


class ResourceComparisonReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_aggregates: list[VariantResourceAggregate] = Field(default_factory=list)
    baseline_aggregate: BaselineResourceAggregate | None = None
    deltas: list[ResourceDelta] = Field(default_factory=list)
    per_variant_assessments: list[VariantBudgetAssessment] = Field(default_factory=list)
    bundle: BundleResourceAggregate | None = None
    bundle_budget_assessment: BundleBudgetAssessment | None = None
    overall_within_budget: bool = False


# ── Failure analysis ──────────────────────────────────────────────────────────


class FailureAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_records: list = Field(default_factory=list)
    failure_summary: str = Field(min_length=1)
    terminal_units: list[str] = Field(default_factory=list)
    retry_patterns: list = Field(default_factory=list)


# ── Reflection ────────────────────────────────────────────────────────────────


class NextRunProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposed_next_action: Literal[
        "conclude_and_report",
        "refine_and_retry",
        "design_new_variant",
        "escalate_to_user",
    ]
    rationale: str = Field(min_length=1)
    suggested_modifications: list[str] = Field(default_factory=list)
    estimated_impact: str = Field(min_length=1)


class ReportFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=IdentifierPattern)
    num_variants: int = Field(ge=0)
    num_successful: int = Field(ge=0)
    num_failed: int = Field(ge=0)
    total_gpu_hours: float = Field(ge=0)
    total_wall_time_seconds: float = Field(ge=0)


class Reflection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    per_variant_conclusions: list[VariantScientificConclusion] = Field(default_factory=list)
    resource_report: ResourceComparisonReport | None = None
    failure_analysis: FailureAnalysis | None = None
    next_run_proposal: NextRunProposal | None = None
    report_facts: ReportFacts | None = None
