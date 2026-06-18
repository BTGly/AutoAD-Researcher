"""WorkspaceChangeBinding — multi-workspace shared change cloning.

When the same PlannedRepositoryChange appears in multiple workspaces
(e.g., shared dependency configuration), clone_shared_changes replicates
the change with workspace-specific metadata while preserving the shared
semantic identity.
"""

from typing import Optional

from autoad_researcher.schemas.patch_planning import (
    PatchPayload, PatchPayloadManifest, PlannedRepositoryChange,
    RepositoryChangePlan, VariantWorkspacePlan,
)


def clone_shared_changes(
    *,
    plan: RepositoryChangePlan,
    target_workspace_id: str,
    change_ids: Optional[list[str]] = None,
) -> list[PlannedRepositoryChange]:
    """Clone shared PlannedRepositoryChange entries into a target workspace.

    Each cloned change gets:
      - A new change_id (prefixed with 'shared_{target_ws}_')
      - workspace_id set to target_workspace_id
      - All other fields preserved (operation_kind, target_mode, path, etc.)

    Args:
        plan: The source RepositoryChangePlan with workspace_plans.
        target_workspace_id: The workspace to clone changes into.
        change_ids: If provided, only clone these specific changes.
                    If None, clone all changes from source workspace_plans.

    Returns:
        List of new PlannedRepositoryChange entries.
    """
    source_change_ids: set[str] = set()
    if change_ids:
        source_change_ids = set(change_ids)
    else:
        for wp in plan.workspace_plans:
            if wp.workspace_id != target_workspace_id:
                source_change_ids.update(wp.planned_change_ids)

    change_map = {c.change_id: c for c in plan.changes}
    clones: list[PlannedRepositoryChange] = []

    for cid in source_change_ids:
        original = change_map.get(cid)
        if original is None:
            continue

        new_cid = f"shared_{target_workspace_id}_{cid}"
        clone = original.model_copy(update={
            "change_id": new_cid,
            "workspace_id": target_workspace_id,
        })
        clones.append(clone)

    return clones


def build_workspace_binding(
    *,
    plan: RepositoryChangePlan,
    target_workspace: VariantWorkspacePlan,
    shared_change_ids: Optional[list[str]] = None,
) -> RepositoryChangePlan:
    """Bind shared changes into a target workspace by cloning them.

    Returns a new RepositoryChangePlan with cloned changes appended.
    The original plan is not modified.
    """
    clones = clone_shared_changes(
        plan=plan,
        target_workspace_id=target_workspace.workspace_id,
        change_ids=shared_change_ids,
    )

    new_changes = list(plan.changes) + clones

    from autoad_researcher.schemas.patch_planning import compute_canonical_plan_sha256

    new_plan = plan.model_copy(update={
        "changes": new_changes,
        "patch_plan_sha256": "",
    })
    new_plan = new_plan.model_copy(update={
        "patch_plan_sha256": compute_canonical_plan_sha256(new_plan),
    })
    return new_plan


def merge_workspace_manifests(
    *,
    manifests: list[PatchPayloadManifest],
    target_workspace_id: str,
) -> Optional[PatchPayloadManifest]:
    """Merge payload manifests from multiple workspaces into one.

    Used when shared changes have been cloned across workspaces and each
    workspace has its own manifest; this produces a unified manifest.
    """
    if not manifests:
        return None

    all_payloads: list[PatchPayload] = []
    combined_payload_ids: set[str] = set()

    for m in manifests:
        for p in m.payloads:
            if p.payload_id not in combined_payload_ids:
                all_payloads.append(p)
                combined_payload_ids.add(p.payload_id)

    from autoad_researcher.code_agent.patch_materializer import build_payload_manifest

    return build_payload_manifest(
        run_id=manifests[0].manifest_id.split("_manifest_")[0] if "_manifest_" in manifests[0].manifest_id else "run",
        workspace_id=target_workspace_id,
        payloads=all_payloads,
        proposed_diff_artifact_id=manifests[0].proposed_diff_artifact_id,
        manifest_id=f"merged_manifest_{target_workspace_id}",
    )
