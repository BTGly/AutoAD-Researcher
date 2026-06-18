"""PatchPlannerAgent — Step 3.6 read-only patch planning.

Takes selected variants from 3.4/3.5 and produces a RepositoryChangePlan
with file-level change descriptions. Does NOT modify the repository.
"""

import hashlib
import json
from pathlib import Path

from autoad_researcher.schemas.baseline_architecture import ModificationHook
from autoad_researcher.schemas.patch_planning import (
    PlannedConfigurationChange,
    PlannedDependencyChange,
    PlannedRepositoryChange,
    PlannedTestChange,
    RepositoryChangePlan,
    SymbolContractDelta,
)
from autoad_researcher.schemas.transfer_design import (
    HookBinding,
    ImplementationVariant,
    InterfaceContractDelta,
)


class PatchPlannerAgent:
    """Read-only agent that maps variants to file-level change plans.

    Uses ModificationHook references from baseline architecture
    and ImplementationVariant hook_bindings as the mapping source.
    Does not execute any code or modify files.
    """

    def __init__(self) -> None:
        pass

    def plan_changes(
        self,
        *,
        run_id: str,
        patch_plan_id: str,
        repository_source_id: str,
        repository_commit: str,
        repository_fingerprint: str,
        idea_id: str,
        selected_variants: list[ImplementationVariant],
        known_hooks: dict[str, ModificationHook],
    ) -> RepositoryChangePlan:
        """Produce a RepositoryChangePlan from selected variants."""
        changes: list[PlannedRepositoryChange] = []
        dep_changes: list[PlannedDependencyChange] = []
        config_changes: list[PlannedConfigurationChange] = []
        test_changes: list[PlannedTestChange] = []
        variant_ids: list[str] = []
        seen_paths: dict[str, set[str]] = {}

        workspace_id = f"ws_{run_id}_default"

        for vi, variant in enumerate(selected_variants):
            variant_ids.append(variant.variant_id)

            for bi, binding in enumerate(variant.hook_bindings):
                hook = known_hooks.get(binding.hook_id)
                if hook is None:
                    continue

                path = hook.module_path

                if path in seen_paths and variant.variant_id in seen_paths[path]:
                    continue
                seen_paths.setdefault(path, set()).add(variant.variant_id)

                change_id = f"chg_{_safe_hash(run_id, variant.variant_id, hook.hook_id, bi)}"

                change = PlannedRepositoryChange(
                    change_id=change_id,
                    workspace_id=workspace_id,
                    change_kind="modify",
                    target_mode="existing_target",
                    hook_id=hook.hook_id,
                    repository_path=path,
                    variant_ids=[variant.variant_id],
                    rationale=f"Variant {variant.variant_label}: {binding.description}",
                    symbol_delta=SymbolContractDelta(
                        module_path=path,
                        symbol_name=hook.symbol or "unknown",
                        current_responsibility=hook.semantic_role,
                        planned_responsibility=f"Augmented: {binding.role}",
                    ),
                    interface_delta=_build_interface_delta(variant),
                    risk_category=variant.risk_level,
                )
                changes.append(change)

            for dep in variant.new_dependencies:
                dep_changes.append(
                    PlannedDependencyChange(
                        dependency_change_id=f"dep_{vi:03d}_{_safe_hash(run_id, dep, '')}",
                        kind="add",
                        package_name=dep,
                        reason=f"Required by variant {variant.variant_label}",
                        variant_ids=[variant.variant_id],
                    )
                )

        plan = RepositoryChangePlan(
            run_id=run_id,
            patch_plan_id=patch_plan_id,
            repository_source_id=repository_source_id,
            repository_commit=repository_commit,
            repository_fingerprint=repository_fingerprint,
            selected_variant_ids=variant_ids,
            idea_id=idea_id,
            changes=changes,
            dependency_changes=dep_changes,
            configuration_changes=config_changes,
            test_changes=test_changes,
            plan_sha256=_compute_plan_sha256(changes, dep_changes),
        )
        return plan


def _build_interface_delta(variant: ImplementationVariant) -> InterfaceContractDelta | None:
    if variant.interface_deltas:
        return variant.interface_deltas[0]
    return None


def _safe_hash(*parts: str) -> str:
    data = "|".join(parts).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:12]


def _compute_plan_sha256(
    changes: list[PlannedRepositoryChange],
    deps: list[PlannedDependencyChange],
) -> str:
    payload = {
        "changes": sorted(
            [(c.change_id, c.repository_path, c.change_kind) for c in changes]
        ),
        "deps": sorted(d.dependency_change_id for d in deps),
    }
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
