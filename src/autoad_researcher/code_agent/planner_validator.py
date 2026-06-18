"""Deterministic PatchPlan validator.

Checks:
  - target_mode / change_kind consistency (model_validators cover this)
  - Path classification against known modifiable/protected sets
  - Hook references resolve to known modification hooks
  - existing_symbol_id verified against symbol table
  - New target paths checked against modifiable scope
  - no protected path violations / ancestor directory checks
  - no system-level writes
"""

from datetime import datetime, timezone

from autoad_researcher.schemas.baseline_architecture import ModificationHook
from autoad_researcher.schemas.patch_planning import (
    PatchPlanValidationIssue,
    PatchPlanValidationReport,
    PlannedRepositoryChange,
    RepositoryChangePlan,
)

_PROTECTED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".env", "dist", "build", ".tox",
}

_SYSTEM_PREFIXES = ("/", "/etc", "/usr", "/bin", "/sbin", "/dev", "/proc", "/sys")


def validate_repository_change_plan(
    *,
    plan: RepositoryChangePlan,
    known_hooks: dict[str, ModificationHook],
    known_symbols: set[str] | None = None,
    modifiable_paths: set[str] | None = None,
    protected_paths: set[str] | None = None,
    report_id: str,
) -> PatchPlanValidationReport:
    """Run all deterministic validations on a RepositoryChangePlan."""
    issues: list[PatchPlanValidationIssue] = []
    mod_set = modifiable_paths or set()
    prot_set = protected_paths or set()
    sym_set = known_symbols or set()

    for i, change in enumerate(plan.changes):
        _validate_change_paths(change, issues, prot_set, i)
        _validate_change_hooks(change, issues, known_hooks, i)
        _validate_existing_symbol(change, issues, sym_set, i)
        _validate_new_target_paths(change, issues, prot_set, mod_set, i)
        _validate_system_paths(change, issues, i)

    status = "passed" if not issues else "failed"
    return PatchPlanValidationReport(
        report_id=report_id,
        run_id=plan.run_id,
        plan_sha256=plan.plan_sha256,
        status=status,
        issues=issues,
        validated_at=datetime.now(timezone.utc),
    )


def _validate_change_paths(
    change: PlannedRepositoryChange,
    issues: list[PatchPlanValidationIssue],
    protected_paths: set[str],
    index: int,
) -> None:
    path = change.repository_path
    parts = [p for p in path.split("/") if p]

    if path in protected_paths:
        issues.append(
            PatchPlanValidationIssue(
                issue_id=f"vi_path_protected_{index:03d}",
                category="protected_path_violation",
                description=f"Change {change.change_id} targets protected path {path}",
                artifact_ids=[change.change_id],
                resolution="blocked",
            )
        )
        return

    for ancestor in _ancestors(path):
        if ancestor in protected_paths:
            issues.append(
                PatchPlanValidationIssue(
                    issue_id=f"vi_ancestor_protected_{index:03d}",
                    category="protected_path_violation",
                    description=(
                        f"Ancestor {ancestor} of {path} is protected"
                    ),
                    artifact_ids=[change.change_id],
                    resolution="blocked",
                )
            )
            return

    for part in parts:
        if part in _PROTECTED_DIRS:
            issues.append(
                PatchPlanValidationIssue(
                    issue_id=f"vi_dir_protected_{index:03d}",
                    category="protected_path_violation",
                    description=(
                        f"Change {change.change_id} path contains protected dir {part!r}"
                    ),
                    artifact_ids=[change.change_id],
                    resolution="blocked",
                )
            )
            return


def _validate_change_hooks(
    change: PlannedRepositoryChange,
    issues: list[PatchPlanValidationIssue],
    known_hooks: dict[str, ModificationHook],
    index: int,
) -> None:
    if change.target_mode != "existing_target":
        return

    if change.hook_id and change.hook_id not in known_hooks:
        issues.append(
            PatchPlanValidationIssue(
                issue_id=f"vi_hook_missing_{index:03d}",
                category="hook_reference_broken",
                description=f"Change {change.change_id} references unknown hook {change.hook_id}",
                artifact_ids=[change.change_id],
                resolution="return_to_3_1",
            )
        )
        return

    if change.hook_id in known_hooks:
        hook = known_hooks[change.hook_id]
        if hook.path_classification in {"protected_candidate", "generated_or_vendor"}:
            issues.append(
                PatchPlanValidationIssue(
                    issue_id=f"vi_hook_protected_{index:03d}",
                    category="path_classification_violation",
                    description=(
                        f"Change {change.change_id} hook {change.hook_id} "
                        f"classified as {hook.path_classification}"
                    ),
                    artifact_ids=[change.change_id],
                    resolution="blocked",
                )
            )
        elif hook.path_classification == "unknown":
            issues.append(
                PatchPlanValidationIssue(
                    issue_id=f"vi_hook_unknown_{index:03d}",
                    category="path_classification_violation",
                    description=(
                        f"Change {change.change_id} hook {change.hook_id} "
                        "path classification unknown"
                    ),
                    artifact_ids=[change.change_id],
                    resolution="return_to_3_1",
                )
            )


def _validate_existing_symbol(
    change: PlannedRepositoryChange,
    issues: list[PatchPlanValidationIssue],
    known_symbols: set[str],
    index: int,
) -> None:
    if change.target_mode != "existing_target":
        return
    if not change.existing_symbol_id and not change.hook_id:
        return

    sym_id = change.existing_symbol_id
    if sym_id:
        if not known_symbols:
            return
        if sym_id not in known_symbols:
            issues.append(
                PatchPlanValidationIssue(
                    issue_id=f"vi_sym_missing_{index:03d}",
                    category="symbol_table_conflict",
                    description=(
                        f"Change {change.change_id} references unknown "
                        f"symbol {sym_id}"
                    ),
                    artifact_ids=[change.change_id],
                    resolution="return_to_3_1",
                )
            )


def _validate_new_target_paths(
    change: PlannedRepositoryChange,
    issues: list[PatchPlanValidationIssue],
    protected_paths: set[str],
    modifiable_paths: set[str],
    index: int,
) -> None:
    if change.target_mode != "new_target":
        return

    path = change.repository_path

    if protected_paths and path in protected_paths:
        issues.append(
            PatchPlanValidationIssue(
                issue_id=f"vi_new_protected_{index:03d}",
                category="protected_path_violation",
                description=f"New target path {path} is in protected set",
                artifact_ids=[change.change_id],
                resolution="blocked",
            )
        )
        return

    found_modifiable = False
    candidate = path
    while True:
        if modifiable_paths and candidate in modifiable_paths:
            found_modifiable = True
            break
        parent = "/".join(candidate.split("/")[:-1])
        if not parent or parent == candidate:
            break
        candidate = parent

    if not found_modifiable and modifiable_paths:
        issues.append(
            PatchPlanValidationIssue(
                issue_id=f"vi_new_not_modifiable_{index:03d}",
                category="path_classification_violation",
                description=(
                    f"New target {path} is not under any modifiable scope"
                ),
                artifact_ids=[change.change_id],
                resolution="return_to_3_1",
            )
        )


def _validate_system_paths(
    change: PlannedRepositoryChange,
    issues: list[PatchPlanValidationIssue],
    index: int,
) -> None:
    for prefix in _SYSTEM_PREFIXES:
        if change.repository_path.startswith(prefix):
            issues.append(
                PatchPlanValidationIssue(
                    issue_id=f"vi_system_path_{index:03d}",
                    category="path_classification_violation",
                    description=(
                        f"Path {change.repository_path} starts with system prefix {prefix}"
                    ),
                    artifact_ids=[change.change_id],
                    resolution="blocked",
                )
            )
            return


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    result = []
    while parts:
        result.append("/".join(parts))
        parts = parts[:-1]
    return result
