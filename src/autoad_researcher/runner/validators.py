"""Step 3.8: Execution validators — sealed service-layer functions.

Matches the sealed contract in docs/3.8开发计划.md v2.12.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autoad_researcher.analysis.metrics import MetricsReport
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
    ResourceUsageReport,
    RunnerIntakeRequest,
    TerminalReason,
    WorkspaceExecutionRef,
    _TERMINAL_REASON_TO_FINAL_STATUS,
)

if TYPE_CHECKING:
    from autoad_researcher.schemas.patch_planning import PatchRunnerHandoff
    from autoad_researcher.supervisor.validity import ScientificValidityReport


# ── Identity ──────────────────────────────────────────────────────────────────


def compute_identity_match(
    prev_identity: AttemptIdentitySnapshot,
    next_identity: AttemptIdentitySnapshot,
) -> bool:
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
    if (ref is None) != (resolved is None):
        raise ValueError(
            f"{name}: artifact ref and resolved artifact must appear together"
        )


# ── Execution status derivation ───────────────────────────────────────────────


def derive_execution_status(
    result: ExperimentExecutionResult | None,
) -> ExecutionStatus:
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
    exec_status = derive_execution_status(execution_result)
    if metrics_report is None:
        metrics_status: str = "not_run"
    elif metrics_report.status == "passed":
        metrics_status = "parsed"
    else:
        metrics_status = "parse_failed"
    if validity_report is None:
        validity_status = "not_run"
    else:
        validity_status = validity_report.status
    return AttemptOutcome(
        execution_status=exec_status,
        metrics_status=metrics_status,
        validity_status=validity_status,
    )


# ── Artifact closure ──────────────────────────────────────────────────────────


def validate_attempt_record_against_artifacts(
    attempt: AttemptRecord,
    expected_run_id: str,
    execution_result: ResolvedArtifact[ExperimentExecutionResult] | None,
    metrics_report: ResolvedArtifact[MetricsReport] | None,
    validity_report: ResolvedArtifact[ScientificValidityReport] | None,
    resource_report: ResolvedArtifact[ResourceUsageReport] | None,
) -> None:
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
        if resource_report.payload.attempt_id != attempt.attempt_id:
            raise ValueError("resource_report.attempt_id != attempt.attempt_id")
        if resource_report.payload.unit_id != attempt.unit_id:
            raise ValueError("resource_report.unit_id != attempt.unit_id")

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


# ── Terminal reason / final status state machine ──────────────────────────────


def derive_terminal_reason(
    outcome: AttemptOutcome,
) -> TerminalReason:
    """Map AttemptOutcome → TerminalReason per v2.12 sealed state machine.

    Completion requires all three fields simultaneously:
      execution=succeeded AND metrics=parsed AND validity=valid.
    """
    if outcome.execution_status == "not_run":
        return "execution_failed"
    if outcome.execution_status == "timeout":
        return "execution_failed"
    if outcome.validity_status == "invalid":
        return "validity_failed"
    if outcome.validity_status == "insufficient_evidence":
        return "insufficient_evidence"
    if (
        outcome.execution_status == "succeeded"
        and outcome.metrics_status == "parsed"
        and outcome.validity_status == "valid"
    ):
        return "completed"
    return "execution_failed"


def derive_final_status(
    terminal_reason: TerminalReason,
) -> ExecutionUnitStatus:
    """Single-source mapping from terminal_reason to ExecutionUnitStatus."""
    return _TERMINAL_REASON_TO_FINAL_STATUS[terminal_reason]


# ── Intake / handoff closure ──────────────────────────────────────────────────


def derive_workspace_execution_refs(
    handoff: PatchRunnerHandoff,
) -> list[WorkspaceExecutionRef]:
    """Derive WorkspaceExecutionRef list deterministically from PatchRunnerHandoff.

    Does not trust caller-supplied workspace_refs — Intake layer derives
    from handoff content and validates with canonical SHA comparison.
    """
    refs: list[WorkspaceExecutionRef] = []

    base = handoff.baseline_workspace_ref
    refs.append(WorkspaceExecutionRef(
        workspace_id=base.workspace_id,
        subject_type="baseline",
        variant_ids=[],
        repository_fingerprint=base.repository_fingerprint,
        repository_commit=base.repository_commit,
        patch_diff_sha256=None,
        local_validation_report_sha256=None,
        patch_application_manifest_ref=None,
        post_patch_validation_report_ref=None,
    ))

    for ws in handoff.variant_workspaces:
        refs.append(WorkspaceExecutionRef(
            workspace_id=ws.workspace_id,
            subject_type="variant",
            variant_ids=list(ws.variant_ids),
            repository_fingerprint=ws.repository_fingerprint,
            repository_commit=handoff.repository_before_commit,
            patch_diff_sha256=ws.patch_diff_sha256,
            local_validation_report_sha256=ws.local_validation_report_sha256,
            patch_application_manifest_ref=ws.patch_application_manifest_ref,
            post_patch_validation_report_ref=ws.post_patch_validation_report_ref,
        ))

    return refs


def validate_intake_against_patch_handoff(
    request: RunnerIntakeRequest,
    handoff: PatchRunnerHandoff,
) -> None:
    """Cross-validate intake workspace_refs against the source PatchRunnerHandoff.

    Derives the expected workspace_refs from handoff and compares their
    canonical SHA to the request's workspace_refs. Reject on mismatch.
    """
    expected = derive_workspace_execution_refs(handoff)
    request_sha = _canonical_sha_list(request.workspace_refs)
    expected_sha = _canonical_sha_list(expected)

    if request_sha != expected_sha:
        raise ValueError("workspace_refs do not match PatchRunnerHandoff")


def _canonical_sha_list(items: list[WorkspaceExecutionRef]) -> str:
    import hashlib
    import json
    data = [
        item.model_dump(mode="json", exclude_none=True)
        for item in items
    ]
    payload = json.dumps(
        data, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── Overall status ────────────────────────────────────────────────────────────


def derive_overall_status(
    manifest: ExecutionManifest,
) -> str:
    completed = sum(
        1 for r in manifest.unit_records
        if r.final_status == ExecutionUnitStatus.COMPLETED
    )
    failed = sum(
        1 for r in manifest.unit_records
        if r.final_status == ExecutionUnitStatus.FAILED
    )
    blocked = sum(
        1 for r in manifest.unit_records
        if r.final_status == ExecutionUnitStatus.BLOCKED
    )
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
    derived_completed = sum(
        1 for r in manifest.unit_records
        if r.final_status == ExecutionUnitStatus.COMPLETED
    )
    derived_failed = sum(
        1 for r in manifest.unit_records
        if r.final_status == ExecutionUnitStatus.FAILED
    )
    derived_blocked = sum(
        1 for r in manifest.unit_records
        if r.final_status == ExecutionUnitStatus.BLOCKED
    )
    if manifest.completed_unit_count != derived_completed:
        raise ValueError("completed_unit_count mismatch")
    if manifest.failed_unit_count != derived_failed:
        raise ValueError("failed_unit_count mismatch")
    if manifest.blocked_unit_count != derived_blocked:
        raise ValueError("blocked_unit_count mismatch")
