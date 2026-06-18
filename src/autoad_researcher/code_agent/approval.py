"""Approval protocol — deterministic validation of approval decisions."""

from autoad_researcher.schemas.patch_planning import (
    ApprovalDecision, ApprovalRequest, PatchPlanValidationReport,
    PlannedRepositoryChange, RepositoryChangePlan,
)


def validate_approval_consistency(
    *, request: ApprovalRequest, decision: ApprovalDecision,
    plan: RepositoryChangePlan,
    validation_report: PatchPlanValidationReport | None = None,
) -> list[str]:
    errors: list[str] = []

    if request.patch_plan_sha256 != plan.plan_sha256:
        errors.append("ApprovalRequest patch_plan_sha256 does not match plan.plan_sha256")
    if decision.approved_patch_plan_sha256 != plan.plan_sha256:
        errors.append("ApprovalDecision approved_patch_plan_sha256 does not match plan.plan_sha256")
    if request.patch_plan_sha256 != decision.approved_patch_plan_sha256:
        errors.append("Decision binds to different SHA than request")

    all_ids = {c.change_id for c in plan.changes}
    approved_set = set(decision.approved_change_ids)
    rejected_set = set(decision.rejected_change_ids)

    if validation_report and validation_report.issues:
        blocked = set()
        for issue in validation_report.issues:
            for aid in (issue.affected_change_ids or issue.artifact_ids):
                blocked.add(aid)
        non_blocked = all_ids - blocked
    else:
        non_blocked = all_ids

    if decision.decision == "approve_all":
        if approved_set - all_ids:
            errors.append("approve_all references change_ids not in plan")
        if approved_set != non_blocked:
            errors.append("approve_all must include all non-blocked change_ids")

    elif decision.decision == "approve_partial":
        if not approved_set:
            errors.append("approve_partial must have approved_change_ids")
        if approved_set - all_ids:
            errors.append("approve_partial references ids not in plan")
        if rejected_set - all_ids:
            errors.append("rejected ids not in plan")
        if approved_set & rejected_set:
            errors.append("ids in both approved and rejected")

    elif decision.decision in {"reject", "revise"}:
        if approved_set:
            errors.append(f"{decision.decision} must not have approved_change_ids")

    approved_paths = set(decision.approved_paths)
    derived = _derive_paths(plan.changes, approved_set)
    if approved_paths != derived:
        missing = derived - approved_paths
        extra = approved_paths - derived
        if missing:
            errors.append(f"approved_paths missing: {sorted(missing)}")
        if extra:
            errors.append(f"approved_paths contains extra: {sorted(extra)}")

    return errors


def validate_approved_paths_against_policy(*, decision: ApprovalDecision, policy_denied_paths: set[str]) -> list[str]:
    errors: list[str] = []
    for path in decision.approved_paths:
        if path in policy_denied_paths:
            errors.append(f"Path {path} is policy-denied")
            continue
        parts = path.split("/")[:-1]
        while parts:
            ancestor = "/".join(parts)
            if ancestor in policy_denied_paths:
                errors.append(f"Ancestor {ancestor} of {path} is policy-denied")
                break
            parts = parts[:-1]
    return errors


def compute_approval_effective_write_paths(
    *, decision: ApprovalDecision, planned_paths: set[str],
    policy_denied_paths: set[str], policy_allowed_paths: set[str] | None = None,
    policy_ask_paths: set[str] | None = None,
) -> dict[str, str]:
    approved_set = set(decision.approved_paths)
    ask_set = policy_ask_paths or set()
    allowed_set = policy_allowed_paths or set()
    result: dict[str, str] = {}
    for path in planned_paths:
        if path in policy_denied_paths:
            result[path] = "deny"
            continue
        for anc in _ancestors(path):
            if anc in policy_denied_paths:
                result[path] = "deny"
                break
        else:
            if path not in approved_set:
                result[path] = "deny"
            elif path in ask_set:
                result[path] = "ask"
            elif allowed_set is None:
                result[path] = "allow"
            elif _path_in_scope(path, allowed_set):
                result[path] = "allow"
            else:
                result[path] = "deny"
    return result


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    result = []
    while parts:
        result.append("/".join(parts))
        parts = parts[:-1]
    return result


def _path_in_scope(path: str, scope: set[str]) -> bool:
    if not scope:
        return False
    candidate = path
    while True:
        if candidate in scope:
            return True
        parts = candidate.split("/")
        if len(parts) <= 1:
            break
        candidate = "/".join(parts[:-1])
    return path in scope


def _derive_paths(changes: list[PlannedRepositoryChange], approved: set[str]) -> set[str]:
    paths: set[str] = set()
    for c in changes:
        if c.change_id in approved:
            paths.add(c.repository_path)
            if c.rename_target_path:
                paths.add(c.rename_target_path)
    return paths
