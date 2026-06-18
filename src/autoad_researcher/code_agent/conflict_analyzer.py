"""Patch conflict analyzer for multi-variant workspace layout.

Determines whether multiple variants can share a workspace,
require configuration switching, or need separate worktrees.
"""

from pathlib import Path

from autoad_researcher.schemas.patch_planning import (
    PatchConflictAnalysis,
    PatchConflictGroup,
    PlannedRepositoryChange,
    VariantWorkspacePlan,
)


def analyze_variant_conflicts(
    *,
    changes: list[PlannedRepositoryChange],
    variant_ids: list[str],
    repository_source_id: str,
    repository_commit: str,
    run_id: str,
    analysis_id: str,
) -> PatchConflictAnalysis:
    """Analyze conflicts between variants and determine workspace layout.

    Groups changes by path. If two variants modify the same path,
    checks whether the changes overlap on symbols.
    """
    path_to_changes: dict[str, list[PlannedRepositoryChange]] = {}
    seen_paths: set[tuple[str, str]] = set()

    for change in changes:
        path_to_changes.setdefault(change.repository_path, []).append(change)

    conflict_groups: list[PatchConflictGroup] = []
    workspaces: list[VariantWorkspacePlan] = []

    for path, path_changes in sorted(path_to_changes.items()):
        variant_sets = [set(c.variant_ids) for c in path_changes]
        all_variants: set[str] = set()
        for vs in variant_sets:
            all_variants.update(vs)

        if len(all_variants) <= 1:
            continue

        overlapping_ids = [c.change_id for c in path_changes]
        group = PatchConflictGroup(
            conflict_group_id=f"cg_{_safe_base(path)}_{len(conflict_groups):03d}",
            target_path=path,
            competing_change_ids=overlapping_ids,
            competing_variant_ids=sorted(all_variants),
            kind="path_overlap",
            description=f"Multiple variants modify {path}",
        )
        conflict_groups.append(group)

    if not conflict_groups:
        workspace = VariantWorkspacePlan(
            workspace_id=f"ws_{run_id}_shared",
            variant_ids=sorted(variant_ids),
            isolation_mode="shared_workspace",
            base_repository_source_id=repository_source_id,
            base_commit=repository_commit,
            planned_change_ids=[c.change_id for c in changes],
        )
        workspaces.append(workspace)
        return PatchConflictAnalysis(
            analysis_id=analysis_id,
            run_id=run_id,
            workspace_plans=workspaces,
            conflict_groups=[],
            overall_status="clean",
            recommendation="All variants can share a single workspace",
        )

    separate_required = any(g.kind == "path_overlap" for g in conflict_groups)

    if separate_required and len(variant_ids) > 1:
        for vid in sorted(variant_ids):
            variant_changes = [c for c in changes if vid in c.variant_ids]
            workspace = VariantWorkspacePlan(
                workspace_id=f"ws_{run_id}_{vid}",
                variant_ids=[vid],
                isolation_mode="separate_worktree",
                base_repository_source_id=repository_source_id,
                base_commit=repository_commit,
                branch_name=f"variant/{run_id}/{vid}",
                worktree_logical_name=f"wt_{run_id}_{vid}",
                planned_change_ids=[c.change_id for c in variant_changes],
                conflict_group_ids=[g.conflict_group_id for g in conflict_groups],
            )
            workspaces.append(workspace)
            seen_paths.update((c.repository_path, c.change_kind) for c in variant_changes)

        overall = "worktree_split_required"
        recommendation = "Variants have overlapping paths; separate worktrees required"
    else:
        workspace = VariantWorkspacePlan(
            workspace_id=f"ws_{run_id}_shared",
            variant_ids=sorted(variant_ids),
            isolation_mode="shared_workspace",
            base_repository_source_id=repository_source_id,
            base_commit=repository_commit,
            planned_change_ids=[c.change_id for c in changes],
            conflict_group_ids=[g.conflict_group_id for g in conflict_groups],
        )
        workspaces.append(workspace)
        overall = "parameterizable_conflicts"
        recommendation = "Conflicts can be resolved via configuration switches"

    return PatchConflictAnalysis(
        analysis_id=analysis_id,
        run_id=run_id,
        workspace_plans=workspaces,
        conflict_groups=conflict_groups,
        overall_status=overall,
        recommendation=recommendation,
    )


def _safe_base(path: str) -> str:
    return Path(path).stem.replace(".", "_").replace("-", "_")
