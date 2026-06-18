"""Patch conflict analyzer for multi-variant workspace layout.

Detects conflicts at both path level and symbol level.
Two variants modifying the same path but different symbols are
"parameterizable" rather than requiring separate worktrees.
"""

from pathlib import Path

from autoad_researcher.schemas.baseline_architecture import ModificationHook
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
    known_hooks: dict[str, ModificationHook] | None = None,
) -> PatchConflictAnalysis:
    """Analyze conflicts at path and symbol level."""
    known = known_hooks or {}

    path_to_changes: dict[str, list[PlannedRepositoryChange]] = {}
    for change in changes:
        path_to_changes.setdefault(change.repository_path, []).append(change)

    conflict_groups: list[PatchConflictGroup] = []
    workspaces: list[VariantWorkspacePlan] = []
    path_overlap_count = 0

    for path, path_changes in sorted(path_to_changes.items()):
        variant_sets = [set(c.variant_ids) for c in path_changes]
        all_variants = set()
        for vs in variant_sets:
            all_variants.update(vs)

        if len(all_variants) <= 1:
            continue

        symbols = set()
        for c in path_changes:
            if c.hook_id and c.hook_id in known:
                symbols.add(f"{c.hook_id}:{known[c.hook_id].symbol or ''}")
            elif c.symbol_delta:
                symbols.add(c.symbol_delta.symbol_name)

        kind = _classify_conflict(path_changes, all_variants, known)

        group = PatchConflictGroup(
            conflict_group_id=f"cg_{_safe_base(path)}_{len(conflict_groups):03d}",
            target_path=path,
            target_symbols=sorted(symbols),
            competing_change_ids=[c.change_id for c in path_changes],
            competing_variant_ids=sorted(all_variants),
            kind=kind,
            description=_describe_conflict(path, kind, symbols),
        )
        conflict_groups.append(group)
        if kind == "mutually_exclusive":
            path_overlap_count += 1

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

    if path_overlap_count > 0 and len(variant_ids) > 1:
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
        overall = "worktree_split_required"
        recommendation = "Mutually exclusive symbol changes require separate worktrees"
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
        recommendation = "Conflicts can be resolved via configuration switches or separate symbols"

    return PatchConflictAnalysis(
        analysis_id=analysis_id,
        run_id=run_id,
        workspace_plans=workspaces,
        conflict_groups=conflict_groups,
        overall_status=overall,
        recommendation=recommendation,
    )


def _classify_conflict(
    path_changes: list[PlannedRepositoryChange],
    all_variants: set[str],
    known_hooks: dict[str, ModificationHook],
) -> str:
    symbol_sets = []
    for c in path_changes:
        symbols = set()
        if c.hook_id and c.hook_id in known_hooks:
            symbols.add(known_hooks[c.hook_id].symbol or c.hook_id)
        elif c.symbol_delta:
            symbols.add(c.symbol_delta.symbol_name)
        elif c.proposed_symbol:
            symbols.add(c.proposed_symbol)
        symbol_sets.append(symbols)

    all_symbols = set()
    for ss in symbol_sets:
        all_symbols.update(ss)

    if len(all_symbols) <= 1:
        return "mutually_exclusive"

    for i in range(len(symbol_sets)):
        for j in range(i + 1, len(symbol_sets)):
            if symbol_sets[i] & symbol_sets[j]:
                return "mutually_exclusive"

    return "parameterizable"


def _describe_conflict(path: str, kind: str, symbols: set[str]) -> str:
    symbol_str = ", ".join(sorted(symbols)) if symbols else "unknown"
    if kind == "mutually_exclusive":
        return f"Variants have overlapping symbol changes in {path}: {symbol_str}"
    if kind == "parameterizable":
        return f"Variants modify different symbols in {path}: {symbol_str}"
    return f"Path overlap in {path}"


def _safe_base(path: str) -> str:
    return Path(path).stem.replace(".", "_").replace("-", "_")
