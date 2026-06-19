"""3.7→3.8 handoff bridge: builds PatchRunnerHandoff and RunnerIntakeRequest from pipeline results."""

from autoad_researcher.runner.validators import (
    derive_workspace_execution_refs,
    validate_intake_against_patch_handoff,
)
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.execution import RunnerIntakeRequest, WorkspaceExecutionRef
from autoad_researcher.schemas.patch_planning import (
    BaselineWorkspaceRef,
    PatchExecutionResult,
    PatchRunnerHandoff,
    PostPatchValidationReport,
    RepositoryChangePlan,
    VariantWorkspaceHandoff,
    canonical_sha,
)


def build_patch_runner_handoff(
    *,
    run_id: str,
    patch_execution_result: PatchExecutionResult,
    plan: RepositoryChangePlan,
    repository_before_commit: str,
    experiment_bundle_ref: str,
    baseline_repository_validation_ref: ArtifactReferenceV2,
    patch_application_manifest_ref: ArtifactReferenceV2,
    post_patch_validation_report_ref: ArtifactReferenceV2,
) -> PatchRunnerHandoff:
    """Build PatchRunnerHandoff v2 from a successful patch execution.

    All artifact refs must be provided by the caller (not fabricated here).
    The function validates that each ref's SHA matches the canonical SHA of
    the corresponding artifact object in ``patch_execution_result``.

    Args:
        run_id: Run identifier.
        patch_execution_result: Result from ControlledPatchApplicator.apply_patch().
        plan: The approved RepositoryChangePlan.
        repository_before_commit: Git commit SHA before the patch.
        experiment_bundle_ref: Reference to the 3.5 experiment bundle.
        baseline_repository_validation_ref: Real ref to the baseline repository
            validation artifact (pre-patch validation). Must not use placeholder SHA.
        patch_application_manifest_ref: Real ref to the patch application
            manifest artifact. SHA must match ``canonical_sha(manifests[0])``.
        post_patch_validation_report_ref: Real ref to the post-patch validation
            report artifact. SHA must match ``canonical_sha(report)`` for the
            report belonging to the same workspace as the manifest.

    Returns:
        A validated PatchRunnerHandoff instance.

    Raises:
        ValueError: if patch_execution_result is not eligible for intake,
            or any artifact ref SHA fails canonical validation.
    """
    if patch_execution_result.next_stage != "eligible_for_runner_intake":
        raise ValueError(
            f"patch execution not eligible for intake: "
            f"overall_status={patch_execution_result.overall_status}, "
            f"next_stage={patch_execution_result.next_stage}"
        )

    if not patch_execution_result.manifests:
        raise ValueError("patch_execution_result has no manifests")

    manifest = patch_execution_result.manifests[0]

    # ── Validate manifest ref SHA ───────────────────────────────────────
    expected_manifest_sha = canonical_sha(manifest)
    if patch_application_manifest_ref.sha256 != expected_manifest_sha:
        raise ValueError(
            f"patch_application_manifest_ref.sha256={patch_application_manifest_ref.sha256} "
            f"does not match canonical_sha(manifests[0])={expected_manifest_sha}"
        )

    # ── Validate post-patch validation report ref SHA ───────────────────
    matching_reports = [
        r for r in patch_execution_result.validation_reports
        if r.workspace_id == manifest.workspace_id
    ]
    if not matching_reports:
        raise ValueError(
            f"no PostPatchValidationReport found for workspace_id={manifest.workspace_id}"
        )
    validation_report = matching_reports[0]
    expected_report_sha = canonical_sha(validation_report)
    if post_patch_validation_report_ref.sha256 != expected_report_sha:
        raise ValueError(
            f"post_patch_validation_report_ref.sha256={post_patch_validation_report_ref.sha256} "
            f"does not match canonical_sha(validation_report)={expected_report_sha}"
        )

    # ── Determine baseline and variant workspaces from the plan ──────────
    baseline_wp = None
    variant_wps = []
    for wp in plan.workspace_plans:
        if not wp.variant_ids:
            baseline_wp = wp
        else:
            variant_wps.append(wp)

    all_variant_ids = sorted(set(
        vid for wp in variant_wps for vid in wp.variant_ids
    ))

    baseline_ref = BaselineWorkspaceRef(
        workspace_id=baseline_wp.workspace_id if baseline_wp else "",
        repository_fingerprint=plan.repository_fingerprint,
        repository_commit=repository_before_commit,
        repository_validation_ref=baseline_repository_validation_ref,
    )

    diff_sha = manifest.patch_diff_sha256 or "0" * 64
    local_validation_report_sha = post_patch_validation_report_ref.sha256

    variant_handoffs: list[VariantWorkspaceHandoff] = []
    for wp in variant_wps:
        variant_handoffs.append(VariantWorkspaceHandoff(
            workspace_id=wp.workspace_id,
            variant_ids=wp.variant_ids,
            repository_fingerprint=plan.repository_fingerprint,
            patch_diff_sha256=diff_sha,
            local_validation_report_sha256=local_validation_report_sha,
            patch_application_manifest_ref=patch_application_manifest_ref,
            post_patch_validation_report_ref=post_patch_validation_report_ref,
        ))

    handoff = PatchRunnerHandoff(
        run_id=run_id,
        repository_before_commit=repository_before_commit,
        approved_patch_plan_sha256=plan.patch_plan_sha256,
        selected_variant_ids=all_variant_ids,
        experiment_bundle_ref=experiment_bundle_ref,
        baseline_workspace_ref=baseline_ref,
        variant_workspaces=variant_handoffs,
    )
    return handoff


def build_runner_intake_request(
    *,
    handoff: PatchRunnerHandoff,
    handoff_artifact_sha256: str,
    experiment_planner_handoff_sha256: str,
    experiment_matrix_sha256: str,
    shared_protocol_fingerprint: str,
    statistical_analysis_plan_sha256: str,
    operational_guard_policy_sha256: str,
) -> RunnerIntakeRequest:
    """Build a RunnerIntakeRequest from a PatchRunnerHandoff.

    Derives workspace_refs from the handoff via derive_workspace_execution_refs,
    then validates the intake against the handoff.
    """
    workspace_refs = derive_workspace_execution_refs(handoff)

    request = RunnerIntakeRequest(
        patch_runner_handoff_ref=ArtifactReferenceV2(
            artifact_id=f"handoff_{handoff.run_id}",
            artifact_type="patch_runner_handoff",
            locator=f"runs/{handoff.run_id}/handoff.json",
            sha256=handoff_artifact_sha256,
        ),
        experiment_planner_handoff_sha256=experiment_planner_handoff_sha256,
        experiment_matrix_sha256=experiment_matrix_sha256,
        shared_protocol_fingerprint=shared_protocol_fingerprint,
        statistical_analysis_plan_sha256=statistical_analysis_plan_sha256,
        operational_guard_policy_sha256=operational_guard_policy_sha256,
        workspace_refs=workspace_refs,
    )

    validate_intake_against_patch_handoff(request, handoff)
    return request
