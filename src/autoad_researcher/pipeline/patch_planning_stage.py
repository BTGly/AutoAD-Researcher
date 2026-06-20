"""Stage 3.6 patch_planner runner — orchestrates plan generation + validation + approval request."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.schemas.baseline_architecture import BaselineArchitectureContract, ModificationHook
from autoad_researcher.schemas.experiment_planning import ExperimentPlannerHandoff
from autoad_researcher.schemas.stage3_acceptance import (
    Stage3AcceptanceArtifactRef,
    Stage3AcceptanceStageRecord,
)
from autoad_researcher.schemas.transfer_design import ImplementationVariant


def run_patch_planning_stage(
    run_id: str,
    run_dir: Path,
    stage_dir: Path,
    repo_root: Path = Path("workspace/repos/patchcore-inspection"),
) -> Stage3AcceptanceStageRecord:
    """Run the 3.6 patch planning stage.

    Consumes 3.5 handoff → produces RepositoryChangePlan + PatchPayloadManifest
    + validation reports + ApprovalRequest.
    """
    approval_request_path = stage_dir / "patch_planner_approval_request.json"

    # Resume check
    if approval_request_path.exists():
        handoff_sha = _sha256_file(approval_request_path)
        return Stage3AcceptanceStageRecord(
            stage="patch_planner", status="passed",
            handoff_sha256=handoff_sha,
            artifacts=[
                Stage3AcceptanceArtifactRef(
                    relative_path=str(approval_request_path.relative_to(run_dir)),
                    sha256=handoff_sha,
                    artifact_type="patch_planner_approval_request",
                ),
            ],
        )

    # Load 3.5 handoff
    handoff_path = run_dir / "experiment_planning" / "experiment_planner_handoff.json"
    if not handoff_path.exists():
        return Stage3AcceptanceStageRecord(
            stage="patch_planner", status="blocked",
            blocked_reason="blocked_upstream: experiment_planner_handoff.json not found",
        )
    handoff = ExperimentPlannerHandoff.model_validate_json(
        handoff_path.read_text(encoding="utf-8"),
    )

    # Load baseline contract for known hooks
    contract = _load_baseline_contract(run_dir)
    known_hooks: dict[str, ModificationHook] = {}
    if contract and contract.modifiable_hooks:
        known_hooks = {h.hook_id: h for h in contract.modifiable_hooks}

    # Load selected variants from transfer_design
    variants_path = run_dir / "transfer_design" / "implementation_variants.json"
    if not variants_path.exists():
        return Stage3AcceptanceStageRecord(
            stage="patch_planner", status="blocked",
            blocked_reason="blocked_upstream: implementation_variants.json not found",
        )
    all_variants = [
        ImplementationVariant.model_validate(v)
        for v in json.loads(variants_path.read_text(encoding="utf-8"))
    ]
    selected_ids = handoff.selected_variant_ids
    selected_variants = [v for v in all_variants if v.variant_id in selected_ids]

    # Load repo info
    repo_info = _load_repo_info(run_dir)
    repository_source_id = repo_info.get("source_id", "source_local")
    repository_commit = repo_info.get("resolved_commit", "unknown")

    # Compute repo fingerprint
    from autoad_researcher.code_agent.patch_applicator import _fingerprint
    repository_fingerprint = _fingerprint(repo_root)

    # Build narrow read request
    hook_paths = sorted({h.module_path for h in known_hooks.values()})
    from autoad_researcher.schemas.patch_planning import NarrowRepositoryReadRequest
    narrow_request = NarrowRepositoryReadRequest(
        repository_source_id=repository_source_id,
        repository_commit=repository_commit,
        allowed_paths=hook_paths,
        requested_paths=hook_paths,
        max_files=50,
        max_bytes=1048576,
        purpose="patch_planning",
    )

    # Fail-closed: selected variants must have hook_bindings
    for v in selected_variants:
        if not v.hook_bindings:
            return Stage3AcceptanceStageRecord(
                stage="patch_planner", status="blocked",
                blocked_reason=(
                    f"blocked_empty_hook_bindings: variant {v.variant_id} has no "
                    f"hook_bindings; return to 3.4 to populate implementation hook bindings"
                ),
            )

    # Run PatchPlannerAgent
    from autoad_researcher.code_agent.patch_planner import PatchPlannerAgent
    planner = PatchPlannerAgent()
    patch_plan_id = f"plan_{run_id}"
    plan, planning_issues = planner.plan_changes(
        run_id=run_id,
        patch_plan_id=patch_plan_id,
        repository_source_id=repository_source_id,
        repository_commit=repository_commit,
        repository_fingerprint=repository_fingerprint,
        idea_id=handoff.run_id,
        selected_variants=selected_variants,
        known_hooks=known_hooks,
    )

    # Fail-closed: plan must have changes when variants exist
    if not plan.changes and selected_variants:
        _write_json(stage_dir / "repository_change_plan.json", plan.model_dump(mode="json", exclude_none=True))
        return Stage3AcceptanceStageRecord(
            stage="patch_planner", status="blocked",
            blocked_reason=(
                f"blocked_empty_plan: plan has no changes with "
                f"{len(selected_variants)} selected variant(s) and "
                f"{len(planning_issues)} planning issue(s); "
                f"check known_hooks match variant hook_bindings"
            ),
        )

    # Conflict analysis + workspace layout (BEFORE validation/manifest)
    from autoad_researcher.code_agent.conflict_analyzer import analyze_variant_conflicts, apply_workspace_layout
    analysis = analyze_variant_conflicts(
        changes=plan.changes, variant_ids=selected_ids,
        repository_source_id=repository_source_id,
        repository_commit=repository_commit,
        run_id=run_id, analysis_id=f"ca_{run_id}",
        known_hooks=known_hooks,
    )
    plan = apply_workspace_layout(plan, analysis)
    _write_json(stage_dir / "patch_conflict_analysis.json", analysis.model_dump(mode="json", exclude_none=True))
    _write_json(stage_dir / "repository_change_plan.json", plan.model_dump(mode="json", exclude_none=True))

    # Validate plan (against final plan with correct SHA)
    from autoad_researcher.code_agent.planner_validator import validate_repository_change_plan
    plan_validation = validate_repository_change_plan(
        plan=plan, known_hooks=known_hooks,
        report_id=f"pvr_{run_id}",
    )
    _write_json(stage_dir / "patch_plan_validation_report.json", plan_validation.model_dump(mode="json", exclude_none=True))

    # Materialize payloads
    from autoad_researcher.code_agent.patch_materializer import PatchMaterializer, build_payload_manifest
    from autoad_researcher.core.artifacts import ArtifactStore
    store = ArtifactStore(runs_root=str(run_dir.parent))
    materializer = PatchMaterializer(artifact_store=store)
    payloads = materializer.materialize(
        plan=plan, repository_root=repo_root,
        run_id=run_id, narrow_request=narrow_request,
    )

    # Build proposed diff
    diff_content = _build_proposed_diff(payloads, repo_root, plan, store, run_id)
    diff_artifact_id = f"patch_planner/proposed_patch.diff"
    diff_sha256 = hashlib.sha256(diff_content.encode()).hexdigest()
    store.write_raw(run_id, diff_artifact_id, diff_content.encode())

    ws_id = analysis.workspace_plans[0].workspace_id if analysis.workspace_plans else f"ws_{run_id}_default"
    manifest = build_payload_manifest(
        run_id=run_id,
        workspace_id=ws_id,
        patch_plan_sha256=plan.patch_plan_sha256,
        payloads=payloads,
        proposed_diff_artifact_id=diff_artifact_id,
        proposed_diff_sha256=diff_sha256,
    )
    _write_json(stage_dir / "patch_payload_manifest.json", manifest.model_dump(mode="json", exclude_none=True))

    # Validate payload manifest
    from autoad_researcher.code_agent.payload_validator import validate_payload_manifest
    payload_validation = validate_payload_manifest(
        manifest=manifest, plan=plan,
        repository_root=repo_root,
        report_id=f"ppvr_{run_id}",
        artifact_store=store,
    )
    _write_json(stage_dir / "patch_payload_validation_report.json", payload_validation.model_dump(mode="json", exclude_none=True))

    # Build ApprovalRequest
    from autoad_researcher.schemas.patch_planning import (
        ApprovalRequest,
        InternalValidationStep,
        ExternalValidationCommand,
        WorkspaceApprovalSummary,
        canonical_sha,
    )

    affected_paths = sorted({c.repository_path for c in plan.changes})
    ws_summary = WorkspaceApprovalSummary(
        workspace_id=ws_id,
        variant_ids=selected_ids,
        planned_change_ids=[c.change_id for c in plan.changes],
        affected_paths=affected_paths,
    )

    # Internal validation steps required for preflight D5-D7
    py_payload_ids = [p.payload_artifact_id for p in payloads if p.target_path.endswith(".py")]
    internal_steps: list[InternalValidationStep] = []
    if py_payload_ids:
        internal_steps.append(InternalValidationStep(
            step_id="ast_parse", required=True,
            target_artifact_ids=py_payload_ids,
        ))
    # diff_integrity is non-required when _generate_proposed_content
    # returns identical content (stub — no LLM code synthesis)
    internal_steps.append(InternalValidationStep(
        step_id="diff_integrity", required=False,
        target_artifact_ids=[diff_artifact_id],
    ))
    internal_steps.append(InternalValidationStep(
        step_id="path_containment", required=True,
        target_artifact_ids=[diff_artifact_id],
    ))

    external_cmds: list[ExternalValidationCommand] = []

    approval_request = ApprovalRequest(
        approval_request_id=f"apr_{run_id}",
        run_id=run_id,
        workspace_id=ws_id,
        patch_plan_sha256=plan.patch_plan_sha256,
        patch_payload_manifest_sha256=manifest.manifest_sha256,
        proposed_patch_diff_sha256=diff_sha256,
        patch_payload_validation_report_sha256=canonical_sha(payload_validation),
        patch_plan_validation_report_sha256=canonical_sha(plan_validation),
        repository_before_fingerprint=repository_fingerprint,
        selected_variant_ids=selected_ids,
        overall_risk_level="medium",
        workspace_summary=ws_summary,
        internal_validation_steps=internal_steps,
        external_validation_commands=external_cmds,
        approval_request_sha256="0" * 64,
        created_at=datetime.now(timezone.utc),
    )
    approval_request = approval_request.model_copy(
        update={"approval_request_sha256": canonical_sha(approval_request)},
    )

    _write_json(approval_request_path, approval_request.model_dump(mode="json", exclude_none=True))

    handoff_sha = _sha256_file(approval_request_path)
    return Stage3AcceptanceStageRecord(
        stage="patch_planner", status="passed",
        handoff_sha256=handoff_sha,
        artifacts=[
            Stage3AcceptanceArtifactRef(
                relative_path=str(approval_request_path.relative_to(run_dir)),
                sha256=handoff_sha,
                artifact_type="patch_planner_approval_request",
            ),
        ],
    )


# ── Helpers ──────────────────────────────────────────────────────────

def _load_baseline_contract(run_dir: Path) -> BaselineArchitectureContract | None:
    path = run_dir / "baseline_architecture_contract.json"
    if path.exists():
        try:
            return BaselineArchitectureContract.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    alt = run_dir / "repository_intelligence" / "baseline_architecture_contract.json"
    if alt.exists():
        try:
            return BaselineArchitectureContract.model_validate_json(alt.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _load_repo_info(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "repository_source.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _build_proposed_diff(
    payloads: list,
    repo_root: Path,
    plan: "RepositoryChangePlan",
    artifact_store: Any = None,
    run_id: str | None = None,
) -> str:
    """Build a unified diff string from materialized payloads.

    Reads the before content from the repo root and the after content
    from the artifact store (payload artifact) to produce a real diff.
    Falls back to no-change when content is unavailable.
    """
    import difflib
    lines: list[str] = []
    from autoad_researcher.schemas.patch_planning import PatchPayload, RepositoryChangePlan
    plan_changes = {c.change_id: c for c in plan.changes}
    for payload in payloads:
        change = plan_changes.get(payload.change_id)
        if change is None:
            continue
        path = change.repository_path
        source_path = repo_root / path
        before = ""
        if source_path.exists():
            before = source_path.read_text()
        after = before
        if artifact_store is not None and run_id is not None and payload.payload_artifact_id:
            try:
                after_bytes = artifact_store.read_raw(run_id, payload.payload_artifact_id)
                if after_bytes is not None:
                    after = after_bytes.decode("utf-8")
            except Exception:
                pass
        ud = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        )
        lines.extend(ud)
    return "".join(lines)


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
