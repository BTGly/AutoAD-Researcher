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
    left_value: float | None = Field(default=None, allow_inf_nan=False)
    right_value: float | None = Field(default=None, allow_inf_nan=False)
    left_parse_status: Literal["parsed", "missing", "invalid", "absent"]
    right_parse_status: Literal["parsed", "missing", "invalid", "absent"]
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
    left_by_name = {metric.metric_name: metric for metric in left.metrics}
    right_by_name = {metric.metric_name: metric for metric in right.metrics}
    comparisons = []
    names = sorted(set(left_by_name) | set(right_by_name))
    for name in names:
        left_metric = left_by_name.get(name)
        right_metric = right_by_name.get(name)
        required = bool(
            (left_metric is not None and left_metric.required)
            or (right_metric is not None and right_metric.required)
        )
        if not required and name not in tolerances:
            continue
        tolerance = tolerances.get(name, 0)
        left_status = left_metric.parse_status if left_metric is not None else "absent"
        right_status = right_metric.parse_status if right_metric is not None else "absent"
        left_value = (
            left_metric.value
            if left_metric is not None and left_metric.parse_status == "parsed"
            else None
        )
        right_value = (
            right_metric.value
            if right_metric is not None and right_metric.parse_status == "parsed"
            else None
        )
        if left_value is None or right_value is None:
            comparisons.append(
                MetricComparison(
                    metric_name=name,
                    required=required,
                    left_value=left_value,
                    right_value=right_value,
                    left_parse_status=left_status,
                    right_parse_status=right_status,
                    absolute_tolerance=tolerance,
                    absolute_difference=tolerance + 1,
                    status="failed",
                )
            )
            continue
        diff = abs(left_value - right_value)
        comparisons.append(
            MetricComparison(
                metric_name=name,
                required=required,
                left_value=left_value,
                right_value=right_value,
                left_parse_status=left_status,
                right_parse_status=right_status,
                absolute_tolerance=tolerance,
                absolute_difference=diff,
                status="passed" if diff <= tolerance else "failed",
            )
        )
    return comparisons
