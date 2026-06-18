"""Deterministic PatchPlan validator.

Checks:
  - target_mode / change_kind consistency (model_validators already cover this)
  - Path classification against known modifiable/protected sets
  - Hook references resolve to known modification hooks
  - no protected path violations
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
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".env",
    "dist",
    "build",
    ".tox",
}

_SYSTEM_PREFIXES = ("/", "/etc", "/usr", "/bin", "/sbin", "/dev", "/proc", "/sys")


def validate_repository_change_plan(
    *,
    plan: RepositoryChangePlan,
    known_hooks: dict[str, ModificationHook],
    modifiable_paths: set[str],
    protected_paths: set[str],
    report_id: str,
) -> PatchPlanValidationReport:
    """Run all deterministic validations on a RepositoryChangePlan."""
    issues: list[PatchPlanValidationIssue] = []

    for i, change in enumerate(plan.changes):
        _validate_change_paths(change, issues, protected_paths, i)
        _validate_change_hooks(change, issues, known_hooks, i)
        _validate_new_target_paths(change, issues, protected_paths, modifiable_paths, i)
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

    for part in parts:
        if part in _PROTECTED_DIRS:
            issues.append(
                PatchPlanValidationIssue(
                    issue_id=f"vi_dir_protected_{index:03d}",
                    category="protected_path_violation",
                    description=f"Change {change.change_id} path contains protected dir {part!r}",
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

    if change.hook_id in known_hooks:
        hook = known_hooks[change.hook_id]
        if hook.path_classification in {"protected_candidate", "generated_or_vendor"}:
            issues.append(
                PatchPlanValidationIssue(
                    issue_id=f"vi_hook_protected_{index:03d}",
                    category="path_classification_violation",
                    description=f"Change {change.change_id} hook {change.hook_id} classified as {hook.path_classification}",
                    artifact_ids=[change.change_id],
                    resolution="blocked",
                )
            )
        elif hook.path_classification == "unknown":
            issues.append(
                PatchPlanValidationIssue(
                    issue_id=f"vi_hook_unknown_{index:03d}",
                    category="path_classification_violation",
                    description=f"Change {change.change_id} hook {change.hook_id} path classification unknown",
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

    if path in protected_paths:
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

    parent = "/".join(path.split("/")[:-1])
    while parent:
        if parent in protected_paths:
            issues.append(
                PatchPlanValidationIssue(
                    issue_id=f"vi_parent_protected_{index:03d}",
                    category="protected_path_violation",
                    description=f"New target parent directory {parent} is protected",
                    artifact_ids=[change.change_id],
                    resolution="blocked",
                )
            )
            break
        parent = "/".join(parent.split("/")[:-1]) if "/" in parent else ""


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
                    description=f"Path {change.repository_path} starts with system prefix {prefix}",
                    artifact_ids=[change.change_id],
                    resolution="blocked",
                )
            )
            return
