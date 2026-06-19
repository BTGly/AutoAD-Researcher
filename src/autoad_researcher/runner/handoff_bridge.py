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
    RepositoryChangePlan,
    VariantWorkspaceHandoff,
)


def build_patch_runner_handoff(
    *,
    run_id: str,
    patch_execution_result: PatchExecutionResult,
    plan: RepositoryChangePlan,
    repository_before_commit: str,
    experiment_bundle_ref: str,
) -> PatchRunnerHandoff:
    """Build PatchRunnerHandoff v2 from a successful patch execution.

    Args:
        run_id: Run identifier.
        patch_execution_result: Result from ControlledPatchApplicator.apply_patch().
        plan: The approved RepositoryChangePlan.
        repository_before_commit: Git commit SHA before the patch.
        experiment_bundle_ref: Reference to the 3.5 experiment bundle.

    Returns:
        A validated PatchRunnerHandoff instance.

    Raises:
        ValueError: if patch_execution_result is not eligible for intake.
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

    # Determine baseline and variant workspaces from the plan
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
        repository_validation_ref=ArtifactReferenceV2(
            artifact_id=f"val_{run_id}_{baseline_wp.workspace_id}" if baseline_wp else "",
            artifact_type="validation_report",
            locator=f"runs/{run_id}/{baseline_wp.workspace_id}/validation.json" if baseline_wp else "",
            sha256="0" * 64,
        ),
    )

    variant_handoffs: list[VariantWorkspaceHandoff] = []
    for wp in variant_wps:
        diff_sha = manifest.patch_diff_sha256 or "0" * 64
        variant_handoffs.append(VariantWorkspaceHandoff(
            workspace_id=wp.workspace_id,
            variant_ids=wp.variant_ids,
            repository_fingerprint=plan.repository_fingerprint,
            patch_diff_sha256=diff_sha,
            local_validation_report_sha256=diff_sha,
            patch_application_manifest_ref=ArtifactReferenceV2(
                artifact_id=f"manifest_{run_id}_{wp.workspace_id}",
                artifact_type="patch_application_manifest",
                locator=f"runs/{run_id}/{wp.workspace_id}/manifest.json",
                sha256=diff_sha,
            ),
            post_patch_validation_report_ref=ArtifactReferenceV2(
                artifact_id=f"post_val_{run_id}_{wp.workspace_id}",
                artifact_type="post_patch_validation_report",
                locator=f"runs/{run_id}/{wp.workspace_id}/post_validation.json",
                sha256=diff_sha,
            ),
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
