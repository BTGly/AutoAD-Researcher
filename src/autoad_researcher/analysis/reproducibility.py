"""Reproducibility comparison for two experiment attempts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.analysis.metrics import MetricsReport
from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.supervisor.validity import ScientificValidityReport


class AttemptEvidenceSummary(BaseModel):
    """Evidence references for one attempt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    attempt_id: str
    repository_fingerprint: str
    case_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    command_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class InvariantCheck(BaseModel):
    """One reproducibility invariant comparison."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str
    required: bool
    status: Literal["passed", "failed"]
    left: str
    right: str


class MetricComparison(BaseModel):
    """One metric comparison between attempts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    metric_name: str
    required: bool
    left_value: float = Field(allow_inf_nan=False)
    right_value: float = Field(allow_inf_nan=False)
    absolute_tolerance: float = Field(ge=0, allow_inf_nan=False)
    absolute_difference: float = Field(ge=0, allow_inf_nan=False)
    status: Literal["passed", "failed"]


class ReproducibilityReport(BaseModel):
    """Final comparison between two attempts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    status: Literal["reproducible", "not_reproducible", "invalid"]
    invariant_checks: list[InvariantCheck]
    metric_comparisons: list[MetricComparison]
    attempt_01_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_02_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def compare_attempts(
    *,
    attempt_01: AttemptEvidenceSummary,
    attempt_02: AttemptEvidenceSummary,
    metrics_01: MetricsReport,
    metrics_02: MetricsReport,
    validity_01: ScientificValidityReport,
    validity_02: ScientificValidityReport,
    metric_tolerances: dict[str, float],
) -> ReproducibilityReport:
    """Compare two attempts across invariants, validity, and metrics."""
    invariants = _compare_invariants(attempt_01, attempt_02)
    metrics = _compare_metrics(metrics_01, metrics_02, metric_tolerances)
    invalid = validity_01.status != "valid" or validity_02.status != "valid"
    failed_invariant = any(check.required and check.status == "failed" for check in invariants)
    failed_metric = any(comp.required and comp.status == "failed" for comp in metrics)

    if invalid:
        status = "invalid"
    elif failed_invariant or failed_metric:
        status = "not_reproducible"
    else:
        status = "reproducible"

    payload = {
        "schema_version": 1,
        "status": status,
        "invariant_checks": [item.model_dump(mode="json") for item in invariants],
        "metric_comparisons": [item.model_dump(mode="json") for item in metrics],
        "attempt_01_sha256": canonical_sha256(attempt_01),
        "attempt_02_sha256": canonical_sha256(attempt_02),
    }
    payload["report_sha256"] = canonical_sha256(payload)
    return ReproducibilityReport.model_validate(payload)


def _compare_invariants(left: AttemptEvidenceSummary, right: AttemptEvidenceSummary) -> list[InvariantCheck]:
    names = [
        "repository_fingerprint",
        "case_sha256",
        "configuration_sha256",
        "environment_sha256",
        "dataset_manifest_sha256",
        "asset_manifest_sha256",
        "command_sha256",
    ]
    checks = []
    for name in names:
        left_value = getattr(left, name)
        right_value = getattr(right, name)
        checks.append(
            InvariantCheck(
                name=name,
                required=True,
                status="passed" if left_value == right_value else "failed",
                left=left_value,
                right=right_value,
            )
        )
    return checks


def _compare_metrics(
    left: MetricsReport,
    right: MetricsReport,
    tolerances: dict[str, float],
) -> list[MetricComparison]:
    right_by_name = {metric.metric_name: metric for metric in right.metrics}
    comparisons = []
    for left_metric in left.metrics:
        if left_metric.parse_status != "parsed" or left_metric.value is None:
            continue
        right_metric = right_by_name.get(left_metric.metric_name)
        if right_metric is None or right_metric.parse_status != "parsed" or right_metric.value is None:
            tolerance = tolerances.get(left_metric.metric_name, 0)
            comparisons.append(
                MetricComparison(
                    metric_name=left_metric.metric_name,
                    required=left_metric.required,
                    left_value=left_metric.value,
                    right_value=left_metric.value,
                    absolute_tolerance=tolerance,
                    absolute_difference=tolerance + 1,
                    status="failed",
                )
            )
            continue
        tolerance = tolerances.get(left_metric.metric_name, 0)
        diff = abs(left_metric.value - right_metric.value)
        comparisons.append(
            MetricComparison(
                metric_name=left_metric.metric_name,
                required=left_metric.required,
                left_value=left_metric.value,
                right_value=right_metric.value,
                absolute_tolerance=tolerance,
                absolute_difference=diff,
                status="passed" if diff <= tolerance else "failed",
            )
        )
    return comparisons
