"""PatchPlannerAgent — Step 3.6 read-only patch planning.

Takes selected variants from 3.4/3.5 and produces a RepositoryChangePlan
with file-level change descriptions. Does NOT modify the repository.
"""

import hashlib
from pathlib import Path

from autoad_researcher.schemas.baseline_architecture import ModificationHook
from autoad_researcher.schemas.patch_planning import (
    PatchPlanValidationIssue,
    PlannedConfigurationChange,
    PlannedDependencyChange,
    PlannedRepositoryChange,
    PlannedTestChange,
    RepositoryChangePlan,
    SymbolContractDelta,
    compute_canonical_plan_sha256,
)
from autoad_researcher.schemas.transfer_design import (
    HookBinding,
    ImplementationVariant,
)


class PatchPlannerAgent:
    """Read-only agent that maps variants to file-level change plans.

    Uses ModificationHook references from baseline architecture
    and ImplementationVariant hook_bindings as the mapping source.
    Collects unknown hook references as plan planning issues.
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
    ) -> tuple[RepositoryChangePlan, list[PatchPlanValidationIssue]]:
        """Produce a RepositoryChangePlan and any planning issues."""
        changes: list[PlannedRepositoryChange] = []
        dep_changes: list[PlannedDependencyChange] = []
        config_changes: list[PlannedConfigurationChange] = []
        test_changes: list[PlannedTestChange] = []
        planning_issues: list[PatchPlanValidationIssue] = []
        variant_ids: list[str] = []
        seen_paths: dict[str, set[str]] = {}

        workspace_id = f"ws_{run_id}_default"

        for vi, variant in enumerate(selected_variants):
            variant_ids.append(variant.variant_id)

            for bi, binding in enumerate(variant.hook_bindings):
                hook = known_hooks.get(binding.hook_id)
                if hook is None:
                    planning_issues.append(
                        PatchPlanValidationIssue(
                            issue_id=f"planner_missing_hook_{vi:03d}_{bi:03d}",
                            category="hook_reference_broken",
                            description=(
                                f"Hook {binding.hook_id} for variant "
                                f"{variant.variant_label} not found in known_hooks"
                            ),
                            artifact_ids=[variant.variant_id, binding.hook_id],
                            resolution="return_to_3_1",
                        )
                    )
                    continue

                path = hook.module_path

                if path in seen_paths and variant.variant_id in seen_paths[path]:
                    continue
                seen_paths.setdefault(path, set()).add(variant.variant_id)

                change_id = f"chg_{_safe_hash(run_id, variant.variant_id, hook.hook_id, str(bi))}"

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
                    interface_delta=_primary_interface_delta(variant),
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
            plan_sha256="",
        )
        plan = plan.model_copy(update={"plan_sha256": compute_canonical_plan_sha256(plan)})
        return plan, planning_issues


def _primary_interface_delta(variant: ImplementationVariant):
    if not variant.interface_deltas:
        return None
    if len(variant.interface_deltas) == 1:
        return variant.interface_deltas[0]
    merged_input = []
    merged_output = []
    for d in variant.interface_deltas:
        merged_input.extend(d.input_deltas)
        merged_output.extend(d.output_deltas)
    from autoad_researcher.schemas.transfer_design import InterfaceContractDelta
    return InterfaceContractDelta(input_deltas=merged_input, output_deltas=merged_output)


def _safe_hash(*parts: str) -> str:
    data = "|".join(parts).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:12]
