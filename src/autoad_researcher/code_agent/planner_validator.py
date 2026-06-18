"""Deterministic PatchPlan validator."""

from datetime import datetime, timezone

from autoad_researcher.schemas.baseline_architecture import ModificationHook
from autoad_researcher.schemas.patch_planning import (
    PatchPlanValidationIssue, PatchPlanValidationReport,
    PlannedRepositoryChange, RepositoryChangePlan,
)

_PROTECTED_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".env", "dist", "build", ".tox"}


def validate_repository_change_plan(
    *, plan: RepositoryChangePlan, known_hooks: dict[str, ModificationHook],
    known_symbols: set[str] | None = None,
    modifiable_paths: set[str] | None = None,
    protected_paths: set[str] | None = None,
    report_id: str,
) -> PatchPlanValidationReport:
    issues: list[PatchPlanValidationIssue] = []
    mod = modifiable_paths or set()
    prot = protected_paths or set()
    sym = known_symbols or set()
    for i, change in enumerate(plan.changes):
        _check_paths(change, issues, prot, i)
        _check_hooks(change, issues, known_hooks, i)
        _check_allowed_for_transfer(change, issues, known_hooks, i)
        _check_hook_path_match(change, issues, known_hooks, i)
        _check_symbol(change, issues, sym, i)
        _check_new_target(change, issues, prot, mod, i)
        _check_system_paths(change, issues, i)
    status = "passed" if not issues else "failed"
    return PatchPlanValidationReport(
        report_id=report_id, run_id=plan.run_id, plan_sha256=plan.plan_sha256,
        status=status, issues=issues, validated_at=datetime.now(timezone.utc),
    )


def _check_paths(change, issues, prot, idx):
    p = change.repository_path
    if p in prot:
        issues.append(PatchPlanValidationIssue(
            issue_id=f"vi_prot_{idx:03d}", category="protected_path_violation",
            description=f"{change.change_id} targets protected {p}",
            artifact_ids=[change.change_id], resolution="blocked"))
        return
    for anc in _ancestors(p):
        if anc in prot:
            issues.append(PatchPlanValidationIssue(
                issue_id=f"vi_anc_{idx:03d}", category="protected_path_violation",
                description=f"ancestor {anc} of {p} protected",
                artifact_ids=[change.change_id], resolution="blocked"))
            return
    for part in p.split("/"):
        if part in _PROTECTED_DIRS:
            issues.append(PatchPlanValidationIssue(
                issue_id=f"vi_dir_{idx:03d}", category="protected_path_violation",
                description=f"{change.change_id} has protected dir {part}",
                artifact_ids=[change.change_id], resolution="blocked"))
            return


def _check_hooks(change, issues, known, idx):
    if change.target_mode != "existing_target":
        return
    if change.hook_id and change.hook_id not in known:
        issues.append(PatchPlanValidationIssue(
            issue_id=f"vi_hk_{idx:03d}", category="hook_reference_broken",
            description=f"{change.change_id} unknown hook {change.hook_id}",
            artifact_ids=[change.change_id], resolution="return_to_3_1"))
        return
    if change.hook_id in known:
        h = known[change.hook_id]
        if h.path_classification in {"protected_candidate", "generated_or_vendor"}:
            issues.append(PatchPlanValidationIssue(
                issue_id=f"vi_hp_{idx:03d}", category="path_classification_violation",
                description=f"{change.change_id} hook {change.hook_id} is {h.path_classification}",
                artifact_ids=[change.change_id], resolution="blocked"))
        elif h.path_classification == "unknown":
            issues.append(PatchPlanValidationIssue(
                issue_id=f"vi_hu_{idx:03d}", category="path_classification_violation",
                description=f"{change.change_id} hook {change.hook_id} unknown classification",
                artifact_ids=[change.change_id], resolution="return_to_3_1"))


def _check_allowed_for_transfer(change, issues, known, idx):
    if change.target_mode != "existing_target" or not change.hook_id:
        return
    h = known.get(change.hook_id)
    if h and not h.allowed_for_transfer_design:
        issues.append(PatchPlanValidationIssue(
            issue_id=f"vi_aft_{idx:03d}", category="policy_violation",
            description=f"{change.change_id} hook {change.hook_id} not allowed for transfer design",
            artifact_ids=[change.change_id], resolution="blocked"))


def _check_hook_path_match(change, issues, known, idx):
    if change.target_mode != "existing_target" or not change.hook_id:
        return
    h = known.get(change.hook_id)
    if h and h.module_path != change.repository_path:
        issues.append(PatchPlanValidationIssue(
            issue_id=f"vi_hpm_{idx:03d}", category="hook_reference_broken",
            description=f"{change.change_id} hook path {h.module_path} != change path {change.repository_path}",
            artifact_ids=[change.change_id], resolution="blocked"))


def _check_symbol(change, issues, known_sym, idx):
    if change.target_mode != "existing_target":
        return
    sid = change.existing_symbol_id
    if not sid:
        return
    if not known_sym:
        issues.append(PatchPlanValidationIssue(
            issue_id=f"vi_stm_{idx:03d}", category="symbol_table_conflict",
            description=f"{change.change_id} has existing_symbol_id={sid} but no symbol table",
            artifact_ids=[change.change_id], resolution="return_to_3_1"))
        return
    if sid not in known_sym:
        issues.append(PatchPlanValidationIssue(
            issue_id=f"vi_sym_{idx:03d}", category="symbol_table_conflict",
            description=f"{change.change_id} unknown symbol {sid}",
            artifact_ids=[change.change_id], resolution="return_to_3_1"))


def _check_new_target(change, issues, prot, mod, idx):
    if change.target_mode != "new_target":
        return
    p = change.repository_path
    if prot and p in prot:
        issues.append(PatchPlanValidationIssue(
            issue_id=f"vi_np_{idx:03d}", category="protected_path_violation",
            description=f"new target {p} is protected",
            artifact_ids=[change.change_id], resolution="blocked"))
        return
    if mod:
        found = False
        candidate = p
        while True:
            if candidate in mod:
                found = True
                break
            parts = candidate.split("/")
            if len(parts) <= 1:
                break
            candidate = "/".join(parts[:-1])
        if not found:
            issues.append(PatchPlanValidationIssue(
                issue_id=f"vi_nm_{idx:03d}", category="path_classification_violation",
                description=f"new target {p} not in modifiable scope",
                artifact_ids=[change.change_id], resolution="return_to_3_1"))


def _check_system_paths(change, issues, idx):
    for prefix in ("/", "/etc", "/usr", "/bin", "/sbin"):
        if change.repository_path.startswith(prefix):
            issues.append(PatchPlanValidationIssue(
                issue_id=f"vi_sys_{idx:03d}", category="path_classification_violation",
                description=f"system path {change.repository_path}",
                artifact_ids=[change.change_id], resolution="blocked"))
            return


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    result = []
    while parts:
        result.append("/".join(parts))
        parts = parts[:-1]
    return result
