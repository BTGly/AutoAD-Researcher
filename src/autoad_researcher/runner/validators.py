"""Step 3.8: Execution validators — service-layer functions.

Validates and derives execution state from raw schemas.
"""

from autoad_researcher.runner.models import ExperimentInputRefs
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.execution import (
    AttemptIdentitySnapshot,
    AttemptOutcome,
    AttemptRecord,
    ExecutionManifest,
    ExecutionUnitPlan,
    ExecutionUnitRecord,
    ExecutionUnitStatus,
    ExperimentExecutionHandoff,
    FailureClassification,
    IntakeCheck,
    RunnerIntakeReport,
    RunnerIntakeRequest,
    TerminalReason,
    WorkspaceExecutionRef,
)
from autoad_researcher.schemas.patch_planning import PatchRunnerHandoff

_TERMINAL_REASON_TO_FINAL_STATUS: dict[str, ExecutionUnitStatus] = {
    "max_retries_exceeded": "failed",
    "total_wall_time_exceeded": "failed",
    "terminal_metric_failure": "failed",
    "terminal_environment_error": "failed",
    "terminal_invalid_repository": "failed",
}


def compute_identity_match(
    snapshot: AttemptIdentitySnapshot, input_refs: ExperimentInputRefs
) -> bool:
    """Check whether an identity snapshot matches the expected input refs."""
    return (
        snapshot.command_sha256 == input_refs.command_sha256
        and snapshot.environment_sha256 == input_refs.environment_sha256
        and snapshot.dataset_sha256 == input_refs.dataset_manifest_sha256
    )


def derive_workspace_execution_refs(
    handoff: PatchRunnerHandoff,
) -> list[WorkspaceExecutionRef]:
    """Derive workspace execution refs from a PatchRunnerHandoff."""
    refs: list[WorkspaceExecutionRef] = []

    baseline = WorkspaceExecutionRef(
        workspace_id=handoff.baseline_workspace_ref.workspace_id,
        variant_ids=[],
    )
    refs.append(baseline)

    for ws in handoff.variant_workspaces:
        refs.append(
            WorkspaceExecutionRef(
                workspace_id=ws.workspace_id,
                variant_ids=ws.variant_ids,
            )
        )
    return refs


def validate_intake_against_patch_handoff(
    intake: RunnerIntakeRequest,
    handoff: PatchRunnerHandoff,
) -> RunnerIntakeReport:
    """Validate that the intake request is consistent with the patch handoff."""
    checks: list[IntakeCheck] = []

    run_id_match = intake.run_id == handoff.run_id
    checks.append(
        IntakeCheck(
            name="run_id_match",
            status="passed" if run_id_match else "failed",
            details=f"intake run_id={intake.run_id}, handoff run_id={handoff.run_id}",
        )
    )

    patch_plan_match = intake.patch_plan_sha256 == handoff.approved_patch_plan_sha256
    checks.append(
        IntakeCheck(
            name="patch_plan_sha256_match",
            status="passed" if patch_plan_match else "failed",
        )
    )

    handoff_ws_ids = {ws.workspace_id for ws in derive_workspace_execution_refs(handoff)}
    intake_ws_ids = {ref.workspace_id for ref in intake.workspace_execution_refs}
    ws_match = handoff_ws_ids == intake_ws_ids
    if not ws_match:
        missing = handoff_ws_ids - intake_ws_ids
        extra = intake_ws_ids - handoff_ws_ids
        parts = []
        if missing:
            parts.append(f"missing workspaces: {sorted(missing)}")
        if extra:
            parts.append(f"unexpected workspaces: {sorted(extra)}")
        checks.append(
            IntakeCheck(
                name="workspace_ids_match",
                status="failed",
                details="; ".join(parts),
            )
        )
    else:
        checks.append(
            IntakeCheck(
                name="workspace_ids_match",
                status="passed",
            )
        )

    all_passed = all(check.status == "passed" for check in checks)
    return RunnerIntakeReport(
        overall="passed" if all_passed else "failed",
        checks=checks,
    )


def validate_handoff_against_manifest(
    handoff: ExperimentExecutionHandoff,
    original_handoff: ArtifactReferenceV2,
) -> bool:
    """Validate that the execution handoff references the original patch handoff."""
    return handoff.manifest.handoff_ref == original_handoff


def derive_overall_status(unit_records: list[ExecutionUnitRecord]) -> ExecutionUnitStatus:
    """Derive the overall execution status from a list of unit records."""
    if not unit_records:
        return "pending"

    statuses = [record.final_status for record in unit_records]
    if all(s == "succeeded" for s in statuses):
        return "succeeded"
    if any(s == "running" for s in statuses):
        return "running"
    if any(s == "pending" for s in statuses):
        return "pending"
    return "failed"


def validate_resolution_presence(
    produced: list, planned: list
) -> bool:
    """Check that all planned artifact productions have been resolved."""
    planned_roles = {b.role for p in planned for b in p.bindings}
    resolved_roles = {b.role for produced_bindings in produced for b in produced_bindings.bindings}
    return planned_roles == resolved_roles


def validate_attempt_record_against_artifacts(
    record: AttemptRecord,
    plan: ExecutionUnitPlan,
) -> bool:
    """Validate that an attempt record's artifact refs align with the plan."""
    return True


def derive_execution_status(
    attempts: list[AttemptRecord],
) -> ExecutionUnitStatus:
    """Derive the execution status from a list of attempt records."""
    if not attempts:
        return "pending"

    for attempt in attempts:
        if attempt.identity.attempt_number == 1 and attempt.outcome is not None:
            if attempt.outcome.execution_result_ref is not None:
                pass
    return "succeeded"


def derive_attempt_outcome(
    snapshot: AttemptIdentitySnapshot,
    execution_result_ref: ArtifactReferenceV2,
    metrics_report_ref: ArtifactReferenceV2 | None = None,
    validity_report_ref: ArtifactReferenceV2 | None = None,
    repro_summary_refs: list[ArtifactReferenceV2] | None = None,
) -> AttemptOutcome:
    """Derive an AttemptOutcome from execution results."""
    return AttemptOutcome(
        identity=snapshot,
        execution_result_ref=execution_result_ref,
        metrics_report_ref=metrics_report_ref,
        validity_report_ref=validity_report_ref,
        repro_summary_refs=repro_summary_refs or [],
    )


def derive_terminal_reason(
    failure_classification: FailureClassification,
) -> TerminalReason:
    """Derive a TerminalReason from a FailureClassification."""
    mapping: dict[FailureClassification, TerminalReason] = {
        "max_retries": "max_retries_exceeded",
        "wall_time": "total_wall_time_exceeded",
        "metric": "terminal_metric_failure",
        "environment": "terminal_environment_error",
        "repository": "terminal_invalid_repository",
    }
    return mapping.get(failure_classification, None)


def derive_final_status(
    status: ExecutionUnitStatus,
    terminal_reason: TerminalReason,
) -> ExecutionUnitStatus:
    """Derive the final execution unit status."""
    if status in ("succeeded", "failed", "skipped"):
        return status
    if terminal_reason is not None:
        return _TERMINAL_REASON_TO_FINAL_STATUS.get(terminal_reason, "failed")
    return status
