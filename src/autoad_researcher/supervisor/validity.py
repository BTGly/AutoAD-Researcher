"""Scientific validity checks for controlled attempts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.analysis.metrics import MetricsReport
from autoad_researcher.runner.models import ExperimentExecutionResult, ExperimentInputRefs


class ValidityCheck(BaseModel):
    """One scientific validity check."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    check_id: str
    status: Literal["passed", "failed", "insufficient_evidence"]
    message: str


class ScientificValidityReport(BaseModel):
    """Aggregate scientific validity report."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    status: Literal["valid", "invalid", "insufficient_evidence"]
    checks: list[ValidityCheck]


def validate_scientific_contract(
    *,
    execution_result: ExperimentExecutionResult,
    input_refs: ExperimentInputRefs,
    metrics_report: MetricsReport,
    expected_repository_fingerprint: str,
    actual_repository_fingerprint: str | None,
    expected_category: str | None,
    actual_category: str | None,
    expected_baseline: str | None,
    actual_baseline: str | None,
    seed_fixed: bool,
    data_path_leak_detected: bool | None,
) -> ScientificValidityReport:
    """Validate that a successful attempt has enough evidence to be trusted."""
    checks = [
        _check(
            "execution_success",
            execution_result.status == "success",
            "execution result must be success",
        ),
        _check(
            "repository_fingerprint",
            actual_repository_fingerprint is not None
            and actual_repository_fingerprint == expected_repository_fingerprint
            and actual_repository_fingerprint == input_refs.repository_fingerprint,
            "repository fingerprint must match expected input refs",
            insufficient=actual_repository_fingerprint is None,
        ),
        _check("environment_sha", bool(input_refs.environment_sha256), "environment SHA exists"),
        _check("dataset_manifest_sha", bool(input_refs.dataset_manifest_sha256), "dataset manifest SHA exists"),
        _check("asset_manifest_sha", bool(input_refs.asset_manifest_sha256), "asset manifest SHA exists"),
        _check("command_sha", bool(input_refs.command_sha256), "command SHA exists"),
        _check(
            "required_metrics",
            metrics_report.status == "passed",
            "required metrics must be parsed from source files",
        ),
        _check(
            "category",
            expected_category is not None
            and actual_category is not None
            and expected_category == actual_category,
            "category must match experiment contract",
            insufficient=expected_category is None or actual_category is None,
        ),
        _check(
            "baseline",
            expected_baseline is not None
            and actual_baseline is not None
            and expected_baseline == actual_baseline,
            "baseline must match experiment contract",
            insufficient=expected_baseline is None or actual_baseline is None,
        ),
        _check("seed", seed_fixed, "seed must be fixed"),
        _check(
            "data_leakage",
            data_path_leak_detected is False,
            "data path leakage must not be detected",
            insufficient=data_path_leak_detected is None,
        ),
        _check("execution_network", True, "execution network is disabled by command schema"),
    ]
    if any(check.status == "failed" for check in checks):
        status = "invalid"
    elif any(check.status == "insufficient_evidence" for check in checks):
        status = "insufficient_evidence"
    else:
        status = "valid"
    return ScientificValidityReport(schema_version=1, status=status, checks=checks)


def _check(
    check_id: str,
    passed: bool,
    message: str,
    *,
    insufficient: bool = False,
) -> ValidityCheck:
    if insufficient:
        return ValidityCheck(
            check_id=check_id,
            status="insufficient_evidence",
            message=message,
        )
    return ValidityCheck(
        check_id=check_id,
        status="passed" if passed else "failed",
        message=message,
    )
