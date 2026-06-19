"""3.7→3.8 handoff bridge: builds PatchRunnerHandoff and RunnerIntakeRequest from pipeline results."""

from autoad_researcher.runner.validators import (
    derive_workspace_execution_refs,
    validate_intake_against_patch_handoff,
)
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.execution import RunnerIntakeRequest, WorkspaceExecutionRef
from autoad_researcher.schemas.patch_planning import (
    BaselineWorkspaceRef,
    PatchApplicationManifest,
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
    baseline_validation_report: PostPatchValidationReport,
    patch_application_manifest_refs_by_workspace: dict[str, ArtifactReferenceV2],
    post_patch_validation_report_refs_by_workspace: dict[str, ArtifactReferenceV2],
) -> PatchRunnerHandoff:
    """Build PatchRunnerHandoff v2 from a successful patch execution.

    All artifact refs must be provided by the caller (not fabricated here).
    The function validates each ref's SHA against the canonical SHA of the
    corresponding artifact object (manifest or validation report) matched by
    ``workspace_id`` from ``patch_execution_result``.

    Args:
        run_id: Run identifier.
        patch_execution_result: Result from ControlledPatchApplicator.apply_patch().
        plan: The approved RepositoryChangePlan.
        repository_before_commit: Git commit SHA before the patch.
        experiment_bundle_ref: Reference to the 3.5 experiment bundle.
        baseline_repository_validation_ref: Real ref to the baseline repository
            validation artifact. SHA must match
            ``canonical_sha(baseline_validation_report)``.
        baseline_validation_report: The baseline workspace's validation report
            object used for canonical SHA binding.
        patch_application_manifest_refs_by_workspace: Mapping from workspace_id
            to the real patch-application-manifest ref for that workspace.
            Every variant workspace in the plan must have an entry.
        post_patch_validation_report_refs_by_workspace: Mapping from workspace_id
            to the real post-patch validation report ref for that workspace.
            Every variant workspace in the plan must have an entry.

    Returns:
        A validated PatchRunnerHandoff instance.

    Raises:
        ValueError: if patch_execution_result is not eligible for intake,
            a workspace is missing its refs, or any ref SHA fails canonical
            validation.
    """
    if patch_execution_result.next_stage != "eligible_for_runner_intake":
        raise ValueError(
            f"patch execution not eligible for intake: "
            f"overall_status={patch_execution_result.overall_status}, "
            f"next_stage={patch_execution_result.next_stage}"
        )

    # ── Validate baseline ref SHA against baseline validation report ──────
    expected_baseline_sha = canonical_sha(baseline_validation_report)
    if baseline_repository_validation_ref.sha256 != expected_baseline_sha:
        raise ValueError(
            f"baseline_repository_validation_ref.sha256="
            f"{baseline_repository_validation_ref.sha256} "
            f"does not match canonical_sha(baseline_validation_report)="
            f"{expected_baseline_sha}"
        )

    # ── Build per-workspace lookup helpers ────────────────────────────────
    manifest_by_ws: dict[str, PatchApplicationManifest] = {
        m.workspace_id: m for m in patch_execution_result.manifests
    }
    report_by_ws: dict[str, PostPatchValidationReport] = {
        r.workspace_id: r for r in patch_execution_result.validation_reports
    }

    # Determine baseline and variant workspaces from the plan
    baseline_wp = None
    variant_wps = []
    for wp in plan.workspace_plans:
        if not wp.variant_ids:
            baseline_wp = wp
        else:
            variant_wps.append(wp)

    if baseline_wp is None:
        raise ValueError(
            "plan has no baseline workspace: no VariantWorkspacePlan with "
            "variant_ids=[] found in plan.workspace_plans"
        )

    if baseline_validation_report.workspace_id != baseline_wp.workspace_id:
        raise ValueError(
            f"baseline_validation_report.workspace_id="
            f"{baseline_validation_report.workspace_id} does not match "
            f"baseline_wp.workspace_id={baseline_wp.workspace_id}"
        )

    all_variant_ids = sorted(set(
        vid for wp in variant_wps for vid in wp.variant_ids
    ))

    baseline_ref = BaselineWorkspaceRef(
        workspace_id=baseline_wp.workspace_id,
        repository_fingerprint=plan.repository_fingerprint,
        repository_commit=repository_before_commit,
        repository_validation_ref=baseline_repository_validation_ref,
    )

    # ── Build per-variant-workspace handoffs ──────────────────────────────
    variant_handoffs: list[VariantWorkspaceHandoff] = []
    for wp in variant_wps:
        ws_id = wp.workspace_id

        if ws_id not in patch_application_manifest_refs_by_workspace:
            raise ValueError(
                f"no patch_application_manifest_ref for workspace_id={ws_id} "
                f"in patch_application_manifest_refs_by_workspace"
            )
        if ws_id not in post_patch_validation_report_refs_by_workspace:
            raise ValueError(
                f"no post_patch_validation_report_ref for workspace_id={ws_id} "
                f"in post_patch_validation_report_refs_by_workspace"
            )

        manifest_ref = patch_application_manifest_refs_by_workspace[ws_id]
        post_validation_ref = post_patch_validation_report_refs_by_workspace[ws_id]

        # ── Validate manifest ref SHA for this workspace ──────────────
        if ws_id not in manifest_by_ws:
            raise ValueError(
                f"no PatchApplicationManifest found for workspace_id={ws_id} "
                f"in patch_execution_result.manifests"
            )
        manifest = manifest_by_ws[ws_id]
        expected_manifest_sha = canonical_sha(manifest)
        if manifest_ref.sha256 != expected_manifest_sha:
            raise ValueError(
                f"patch_application_manifest_refs_by_workspace[{ws_id}].sha256="
                f"{manifest_ref.sha256} does not match "
                f"canonical_sha(manifests[{ws_id}])={expected_manifest_sha}"
            )

        # ── Validate post-patch validation report ref SHA for this ws ─
        if ws_id not in report_by_ws:
            raise ValueError(
                f"no PostPatchValidationReport found for workspace_id={ws_id} "
                f"in patch_execution_result.validation_reports"
            )
        validation_report = report_by_ws[ws_id]
        expected_report_sha = canonical_sha(validation_report)
        if post_validation_ref.sha256 != expected_report_sha:
            raise ValueError(
                f"post_patch_validation_report_refs_by_workspace[{ws_id}].sha256="
                f"{post_validation_ref.sha256} does not match "
                f"canonical_sha(validation_reports[{ws_id}])={expected_report_sha}"
            )

        diff_sha = manifest.patch_diff_sha256 or "0" * 64
        local_validation_report_sha = post_validation_ref.sha256

        variant_handoffs.append(VariantWorkspaceHandoff(
            workspace_id=ws_id,
            variant_ids=wp.variant_ids,
            repository_fingerprint=plan.repository_fingerprint,
            patch_diff_sha256=diff_sha,
            local_validation_report_sha256=local_validation_report_sha,
            patch_application_manifest_ref=manifest_ref,
            post_patch_validation_report_ref=post_validation_ref,
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
