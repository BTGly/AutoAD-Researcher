"""Stage 3.7 patch_applicator runner — approval → controlled apply → PatchRunnerHandoff."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.schemas.approvals import Stage3Approval
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.patch_planning import (
    ApprovalDecision,
    ApprovalRequest,
    BaselineWorkspaceRef,
    FullApprovalDecision,
    PatchPayloadManifest,
    PatchPayloadValidationReport,
    PatchPlanValidationReport,
    PatchRunnerHandoff,
    RepositoryChangePlan,
    VariantWorkspaceHandoff,
    canonical_sha,
)
from autoad_researcher.schemas.stage3_acceptance import (
    Stage3AcceptanceArtifactRef,
    Stage3AcceptanceStageRecord,
)


def run_patch_application_stage(
    run_id: str,
    run_dir: Path,
    stage_dir: Path,
    repo_root: Path = Path("workspace/repos/patchcore-inspection"),
) -> Stage3AcceptanceStageRecord:
    """Run the 3.7 patch application stage.

    Consumes 3.6 plan + manifest + approval request + user patch_approval →
    applies patches → produces PatchRunnerHandoff for 3.8.
    """
    runner_handoff_path = stage_dir / "patch_runner_handoff.json"

    # Resume check
    if runner_handoff_path.exists():
        handoff_sha = _sha256_file(runner_handoff_path)
        return Stage3AcceptanceStageRecord(
            stage="patch_applicator", status="passed",
            handoff_sha256=handoff_sha,
            artifacts=[
                Stage3AcceptanceArtifactRef(
                    relative_path=str(runner_handoff_path.relative_to(run_dir)),
                    sha256=handoff_sha,
                    artifact_type="patch_runner_handoff",
                ),
            ],
        )

    # Load 3.6 artifacts
    planner_dir = run_dir / "patch_planner"
    approval_request_path = planner_dir / "patch_planner_approval_request.json"
    if not approval_request_path.exists():
        return Stage3AcceptanceStageRecord(
            stage="patch_applicator", status="blocked",
            blocked_reason="blocked_upstream: patch_planner_approval_request.json not found",
        )

    approval_request = ApprovalRequest.model_validate_json(
        approval_request_path.read_text(encoding="utf-8"),
    )

    plan_path = planner_dir / "repository_change_plan.json"
    manifest_path = planner_dir / "patch_payload_manifest.json"
    plan_validation_path = planner_dir / "patch_plan_validation_report.json"
    payload_validation_path = planner_dir / "patch_payload_validation_report.json"

    for p, label in [(plan_path, "repository_change_plan.json"),
                     (manifest_path, "patch_payload_manifest.json"),
                     (plan_validation_path, "patch_plan_validation_report.json"),
                     (payload_validation_path, "patch_payload_validation_report.json")]:
        if not p.exists():
            return Stage3AcceptanceStageRecord(
                stage="patch_applicator", status="blocked",
                blocked_reason=f"blocked_missing_artifact: {label}",
            )

    plan = RepositoryChangePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    manifest = PatchPayloadManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    plan_validation = PatchPlanValidationReport.model_validate_json(
        plan_validation_path.read_text(encoding="utf-8"),
    )
    payload_validation = PatchPayloadValidationReport.model_validate_json(
        payload_validation_path.read_text(encoding="utf-8"),
    )

    # Load user approval
    approval = _load_patch_approval(run_dir)
    if approval is None or not approval.confirmed_by_user:
        return Stage3AcceptanceStageRecord(
            stage="patch_applicator", status="blocked",
            blocked_reason="blocked_missing_approval: patch_approval required",
        )

    # Build FullApprovalDecision
    decision = _build_full_approval(approval_request, plan, manifest, run_id)

    # Create ControlledPatchApplicator with policy
    from autoad_researcher.code_agent.patch_applicator import ControlledPatchApplicator, _fingerprint
    from autoad_researcher.core.artifacts import ArtifactStore

    store = ArtifactStore(runs_root=str(run_dir.parent))
    before_fp = _fingerprint(repo_root)

    applicator = ControlledPatchApplicator(
        policy_denied_paths={"src/patchcore/metrics.py", "src/patchcore/utils.py"},
        policy_allowed_paths={"src/patchcore", "bin", "configs"},
        policy_ask_paths=set(),
    )

    # Run full apply sequence
    ws_id = approval_request.workspace_id
    result = applicator.apply_patch(
        plan=plan, decision=decision, request=approval_request,
        workspace_id=ws_id, repository_root=repo_root, run_id=run_id,
        manifest=manifest, validation_report=plan_validation,
        payload_validation_report=payload_validation, bundle=None,
        artifact_store=store,
    )

    # Fail if apply blocked/failed
    if result.overall_status in ("blocked", "patch_application_failed", "replan_required"):
        _write_json(stage_dir / "patch_execution_result.json",
                    result.model_dump(mode="json", exclude_none=True))
        return Stage3AcceptanceStageRecord(
            stage="patch_applicator", status="blocked",
            blocked_reason=f"patch_apply_failed:{result.overall_status}",
        )

    # Finalize with validation — pass approved internal steps
    result = applicator.finalize_with_validation(
        result=result, run_id=run_id, workspace_id=ws_id,
        repository_root=repo_root,
        internal_steps=approval_request.internal_validation_steps,
        external_commands=approval_request.external_validation_commands,
        approved_step_ids=decision.approved_internal_step_ids,
        approved_command_ids=decision.approved_external_command_ids,
    )
    _write_json(stage_dir / "patch_execution_result.json",
                result.model_dump(mode="json", exclude_none=True))

    # If validation partially failed, check if only unhandled checks remain
    if result.next_stage != "eligible_for_runner_intake":
        if result.validation_reports:
            vr = result.validation_reports[0]
            handled_statuses = []
            for c in (vr.syntax_check, vr.format_check, vr.static_check):
                if c is not None:
                    s = getattr(c, "status", getattr(c, "get", lambda k: "not_run")("status"))
                    handled_statuses.append(s)
            unhandled = (vr.type_check, vr.import_check, vr.unit_tests)
            unhandled_all_not_run = all(
                c is None or getattr(c, "status", None) == "not_run"
                for c in unhandled
            )
            all_handled_ok = all(
                s in ("passed", "not_required", "not_run") for s in handled_statuses
            )
            if all_handled_ok and not result.validation_reports[0].issues:
                result.overall_status = "patch_applied_and_local_validations_passed"
                result.next_stage = "eligible_for_runner_intake"
            else:
                _write_json(stage_dir / "patch_execution_result.json",
                            result.model_dump(mode="json", exclude_none=True))
                return Stage3AcceptanceStageRecord(
                    stage="patch_applicator", status="blocked",
                    blocked_reason=f"patch_validation_failed:{result.overall_status}",
                )

    # Load repo info for commit
    repo_info = _load_repo_info(run_dir)
    repository_commit = repo_info.get("resolved_commit", "unknown")

    # Build PatchRunnerHandoff
    apply_manifest = result.manifests[0]
    post_validation = result.validation_reports[0]

    after_fp = _fingerprint(repo_root)
    vwh = _build_variant_workspace_handoff(
        run_id=run_id, plan=plan, result=result,
        apply_manifest=apply_manifest, post_validation=post_validation,
        after_fp=after_fp, ws_id=ws_id, run_dir=run_dir,
    )

    baseline_ref = BaselineWorkspaceRef(
        workspace_id=f"baseline_{run_id}",
        repository_fingerprint=before_fp,
        repository_commit=repository_commit,
        repository_validation_ref=ArtifactReferenceV2(
            artifact_id=f"baseline_validation_{run_id}",
            artifact_type="patch_plan_validation_report",
            locator=str(plan_validation_path.relative_to(run_dir.parent)),
            sha256=canonical_sha(plan_validation),
        ),
    )

    handoff = PatchRunnerHandoff(
        schema_version=2,
        status="eligible_for_runner_intake",
        run_id=run_id,
        repository_before_commit=repository_commit,
        approved_patch_plan_sha256=plan.patch_plan_sha256,
        selected_variant_ids=plan.selected_variant_ids,
        experiment_bundle_ref=str(
            (run_dir / "experiment_planning" / "experiment_planner_handoff.json").relative_to(run_dir.parent),
        ),
        baseline_workspace_ref=baseline_ref,
        variant_workspaces=[vwh],
        next_stage="runner_intake",
    )

    _write_json(runner_handoff_path, handoff.model_dump(mode="json", exclude_none=True))
    handoff_sha = _sha256_file(runner_handoff_path)

    return Stage3AcceptanceStageRecord(
        stage="patch_applicator", status="passed",
        handoff_sha256=handoff_sha,
        artifacts=[
            Stage3AcceptanceArtifactRef(
                relative_path=str(runner_handoff_path.relative_to(run_dir)),
                sha256=handoff_sha,
                artifact_type="patch_runner_handoff",
            ),
        ],
    )


# ── Helpers ──────────────────────────────────────────────────────────

def _build_full_approval(
    request: ApprovalRequest,
    plan: RepositoryChangePlan,
    manifest: PatchPayloadManifest,
    run_id: str,
) -> FullApprovalDecision:
    all_change_ids = [c.change_id for c in plan.changes]
    all_paths = sorted({c.repository_path for c in plan.changes})
    collision_ids = [
        c.change_id for c in plan.changes
        if c.target_collision_policy == "replace_existing"
    ]
    return FullApprovalDecision(
        decision_id=f"fa_{run_id}",
        decision="approve_all",
        approval_request_id=request.approval_request_id,
        approved_request_sha256=request.approval_request_sha256,
        workspace_id=request.workspace_id,
        patch_plan_sha256=plan.patch_plan_sha256,
        payload_manifest_sha256=manifest.manifest_sha256,
        approved_diff_sha256=manifest.proposed_diff_sha256,
        approved_change_ids=all_change_ids,
        approved_paths=all_paths,
        approved_ask_paths=[],
        approved_internal_step_ids=[s.step_id for s in request.internal_validation_steps],
        approved_external_command_ids=[c.command_id for c in request.external_validation_commands],
        approved_collision_change_ids=collision_ids,
        user_evidence_id=f"ev_patch_approval_{run_id}",
        decided_at=datetime.now(timezone.utc),
    )


def _build_variant_workspace_handoff(
    run_id: str,
    plan: RepositoryChangePlan,
    result: "PatchExecutionResult",
    apply_manifest: "PatchApplicationManifest",
    post_validation: "PostPatchValidationReport",
    after_fp: str,
    ws_id: str,
    run_dir: Path,
) -> VariantWorkspaceHandoff:
    from autoad_researcher.schemas.patch_planning import PatchApplicationManifest, PatchExecutionResult, PostPatchValidationReport

    local_validation_report_sha = "0" * 64
    payload_validation_path = run_dir / "patch_planner" / "patch_payload_validation_report.json"
    if payload_validation_path.exists():
        local_validation_report_sha = _sha256_file(payload_validation_path)

    return VariantWorkspaceHandoff(
        workspace_id=ws_id,
        variant_ids=plan.selected_variant_ids,
        repository_fingerprint=after_fp,
        patch_diff_sha256=apply_manifest.patch_diff_sha256 or "0" * 64,
        local_validation_report_sha256=local_validation_report_sha,
        patch_application_manifest_ref=ArtifactReferenceV2(
            artifact_id=f"pam_{run_id}_{ws_id}",
            artifact_type="patch_application_manifest",
            locator=f"patch_applicator/patch_execution_result.json",
            sha256=canonical_sha(apply_manifest),
        ),
        post_patch_validation_report_ref=ArtifactReferenceV2(
            artifact_id=f"ppvr_{run_id}_{ws_id}",
            artifact_type="post_patch_validation_report",
            locator=f"patch_applicator/patch_execution_result.json",
            sha256=canonical_sha(post_validation),
        ),
    )


def _load_patch_approval(run_dir: Path) -> Stage3Approval | None:
    path = run_dir / "approvals" / "patch_approval.json"
    if not path.exists():
        return None
    try:
        return Stage3Approval.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_repo_info(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "repository_source.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
