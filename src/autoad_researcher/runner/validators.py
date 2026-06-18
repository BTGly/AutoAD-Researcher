"""Step 3.8: Execution validators — sealed service-layer functions.

Matches the sealed contract in docs/3.8开发计划.md v2.12.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autoad_researcher.analysis.metrics import MetricsReport, ParsedMetric
from autoad_researcher.runner.models import ExperimentExecutionResult
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2, ResolvedArtifact
from autoad_researcher.schemas.execution import (
    AttemptIdentitySnapshot,
    AttemptOutcome,
    AttemptRecord,
    ExecutionManifest,
    ExecutionStatus,
    ExecutionUnitRecord,
    ExecutionUnitStatus,
    TerminalReason,
)

if TYPE_CHECKING:
    from autoad_researcher.supervisor.validity import ScientificValidityReport


def compute_identity_match(
    prev_identity: AttemptIdentitySnapshot,
    next_identity: AttemptIdentitySnapshot,
) -> bool:
    """Four-field identity comparison for retry eligibility.

    All four canonical fields must match for a retry to be the same
    logical operation. This replaces the earlier 3-field comparison
    that omitted repository fingerprint.
    """
    return (
        prev_identity.execution_unit_plan_sha256 == next_identity.execution_unit_plan_sha256
        and prev_identity.command_sha256 == next_identity.command_sha256
        and prev_identity.input_refs_sha256 == next_identity.input_refs_sha256
        and prev_identity.workspace_repository_fingerprint
        == next_identity.workspace_repository_fingerprint
    )


def validate_resolution_presence(
    ref: ArtifactReferenceV2 | None,
    resolved: ResolvedArtifact | None,
    name: str,
) -> None:
    """Artifact ref and resolved payload must appear together or both absent.

    Forbids: ref without resolved (dangling reference, artifact not loaded),
             resolved without ref (payload source untraceable).
    """
    if (ref is None) != (resolved is None):
        raise ValueError(
            f"{name}: artifact ref and resolved artifact must appear together"
        )


def derive_execution_status(
    result: ExperimentExecutionResult | None,
) -> ExecutionStatus:
    """Map 3.0 ExperimentExecutionResult.status to 3.8 AttemptOutcome.execution_status.

    3.0 status values:
      preflight_failed / execution_failed / metric_parse_failed /
      invalid_repository_mutation / success
    3.0 timed_out: bool (independent field)
    """
    if result is None:
        return "not_run"
    if result.timed_out:
        return "timeout"
    if result.status == "success":
        return "succeeded"
    return "failed"


def derive_attempt_outcome(
    execution_result: ExperimentExecutionResult | None,
    metrics_report: MetricsReport | None,
    validity_report: ScientificValidityReport | None,
) -> AttemptOutcome:
    """Derive AttemptOutcome from resolved artifact content.

    outcome is NOT an independent writable fact — it MUST match
    what validate_attempt_record_against_artifacts re-derives.
    """
    exec_status = derive_execution_status(execution_result)
    if metrics_report is None or metrics_report.status not in ("passed", "failed"):
        metrics_status = "not_run"
    else:
        metrics_status = "passed" if metrics_report.status == "passed" else "failed"
    if validity_report is None:
        validity_status = "not_run"
    else:
        validity_status = validity_report.status
    return AttemptOutcome(
        execution_status=exec_status,
        metrics_status=metrics_status,
        validity_status=validity_status,
    )


def validate_attempt_record_against_artifacts(
    attempt: AttemptRecord,
    expected_run_id: str,
    execution_result: ResolvedArtifact[ExperimentExecutionResult] | None,
    metrics_report: ResolvedArtifact[MetricsReport] | None,
    validity_report: ResolvedArtifact[ScientificValidityReport] | None,
    resource_report: ResolvedArtifact | None,
) -> None:
    """Full artifact identity closure for one attempt.

    Dereferences the four attempt artifact refs, verifies SHA binding,
    cross-checks run_id/attempt_id/command_sha256/unit_id, re-derives
    outcome from artifact content, and asserts stored outcome matches.
    """
    validate_resolution_presence(
        attempt.execution_result_ref, execution_result, "execution_result",
    )
    validate_resolution_presence(
        attempt.metrics_report_ref, metrics_report, "metrics_report",
    )
    validate_resolution_presence(
        attempt.validity_report_ref, validity_report, "validity_report",
    )
    validate_resolution_presence(
        attempt.resource_usage_ref, resource_report, "resource_report",
    )

    if execution_result is not None:
        if execution_result.ref.sha256 != attempt.execution_result_ref.sha256:
            raise ValueError("execution_result ref.sha256 mismatch")
        if execution_result.verified_sha256 != execution_result.ref.sha256:
            raise ValueError("execution_result verified SHA mismatch")
        if execution_result.payload.run_id != expected_run_id:
            raise ValueError("execution_result.run_id != expected_run_id")
        if execution_result.payload.attempt != attempt.attempt_id:
            raise ValueError("execution_result.attempt != attempt.attempt_id")
        if execution_result.payload.command_sha256 != attempt.identity.command_sha256:
            raise ValueError("execution_result.command_sha256 != attempt.identity.command_sha256")

    if metrics_report is not None:
        if metrics_report.ref.sha256 != attempt.metrics_report_ref.sha256:
            raise ValueError("metrics_report ref.sha256 mismatch")
        if metrics_report.verified_sha256 != metrics_report.ref.sha256:
            raise ValueError("metrics_report verified SHA mismatch")

    if validity_report is not None:
        if validity_report.ref.sha256 != attempt.validity_report_ref.sha256:
            raise ValueError("validity_report ref.sha256 mismatch")
        if validity_report.verified_sha256 != validity_report.ref.sha256:
            raise ValueError("validity_report verified SHA mismatch")

    if resource_report is not None:
        if resource_report.ref.sha256 != attempt.resource_usage_ref.sha256:
            raise ValueError("resource_report ref.sha256 mismatch")
        if resource_report.verified_sha256 != resource_report.ref.sha256:
            raise ValueError("resource_report verified SHA mismatch")

    derived = derive_attempt_outcome(
        execution_result.payload if execution_result else None,
        metrics_report.payload if metrics_report else None,
        validity_report.payload if validity_report else None,
    )
    if attempt.outcome != derived:
        raise ValueError(
            f"AttemptOutcome does not match referenced artifacts: "
            f"stored={attempt.outcome}, derived={derived}"
        )

    for binding in attempt.resolved_bindings:
        if binding.artifact_ref.sha256 != binding.artifact_sha256:
            raise ValueError("resolved_binding SHA mismatch")

    for prod in attempt.produced_artifacts:
        for binding in prod.bindings:
            if binding.artifact_ref.sha256 != binding.artifact_sha256:
                raise ValueError("produced_artifact SHA mismatch")


def derive_terminal_reason_from_outcome(
    outcome: AttemptOutcome,
) -> TerminalReason:
    """Derive terminal_reason from the final attempt's outcome."""
    if outcome.execution_status == "succeeded":
        if outcome.validity_status in ("valid", "insufficient_evidence"):
            return "completed"
        if outcome.validity_status == "invalid":
            return "validity_failed"
        return "insufficient_evidence"
    if outcome.execution_status == "timeout":
        return "execution_failed"
    if outcome.execution_status == "not_run":
        return "insufficient_evidence"
    return "execution_failed"


def derive_final_status(
    terminal_reason: TerminalReason | None,
) -> ExecutionUnitStatus:
    """Derive final unit status from terminal reason."""
    if terminal_reason == "completed":
        return ExecutionUnitStatus.COMPLETED
    if terminal_reason in (
        "execution_failed",
        "validity_failed",
        "insufficient_evidence",
    ):
        return ExecutionUnitStatus.FAILED
    if terminal_reason in (
        "blocked_upstream_failure",
        "intake_failed",
        "preflight_failed",
    ):
        return ExecutionUnitStatus.BLOCKED
    return ExecutionUnitStatus.FAILED


def derive_overall_status(
    manifest: ExecutionManifest,
) -> str:
    """Derive overall execution status from unit records."""
    completed = sum(1 for r in manifest.unit_records if r.final_status == ExecutionUnitStatus.COMPLETED)
    failed = sum(1 for r in manifest.unit_records if r.final_status == ExecutionUnitStatus.FAILED)
    blocked = sum(1 for r in manifest.unit_records if r.final_status == ExecutionUnitStatus.BLOCKED)
    total = len(manifest.unit_records)
    if completed == total:
        return "completed"
    if blocked == total:
        return "blocked"
    if failed == total:
        return "failed"
    return "partially_completed"


def validate_handoff_against_manifest(
    manifest: ExecutionManifest,
) -> None:
    """Validate that handoff counts match manifest."""
    derived_completed = sum(1 for r in manifest.unit_records if r.final_status == ExecutionUnitStatus.COMPLETED)
    derived_failed = sum(1 for r in manifest.unit_records if r.final_status == ExecutionUnitStatus.FAILED)
    derived_blocked = sum(1 for r in manifest.unit_records if r.final_status == ExecutionUnitStatus.BLOCKED)
    if manifest.completed_unit_count != derived_completed:
        raise ValueError("completed_unit_count mismatch")
    if manifest.failed_unit_count != derived_failed:
        raise ValueError("failed_unit_count mismatch")
    if manifest.blocked_unit_count != derived_blocked:
        raise ValueError("blocked_unit_count mismatch")
