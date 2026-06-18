"""Approval protocol — deterministic validation of approval decisions (schema v2)."""

from autoad_researcher.schemas.patch_planning import (
    ApprovalDecision, ApprovalRequest, FullApprovalDecision,
    PartialApprovalDecision, PatchPlanValidationReport,
    PlannedRepositoryChange, RejectDecision, RepositoryChangePlan,
)


def validate_approval_consistency(
    *, request: ApprovalRequest, decision: ApprovalDecision,
    plan: RepositoryChangePlan,
    validation_report: PatchPlanValidationReport | None = None,
) -> list[str]:
    errors: list[str] = []

    if request.patch_plan_sha256 != plan.patch_plan_sha256:
        errors.append("ApprovalRequest patch_plan_sha256 does not match plan.patch_plan_sha256")
    if _decision_plan_sha(decision) != plan.patch_plan_sha256:
        errors.append("ApprovalDecision patch_plan_sha256 does not match plan.patch_plan_sha256")
    if request.patch_plan_sha256 != _decision_plan_sha(decision):
        errors.append("Decision binds to different SHA than request")

    if request.workspace_id != _decision_ws(decision):
        errors.append(f"Decision workspace {_decision_ws(decision)} != request workspace {request.workspace_id}")

    all_ids = {c.change_id for c in plan.changes}

    if isinstance(decision, FullApprovalDecision):
        approved_set = set(decision.approved_change_ids)
        if approved_set - all_ids:
            errors.append("full approval references change_ids not in plan")
        if validation_report and validation_report.issues:
            # v1.5.8: issues no longer carry affected_change_ids/artifact_ids
            if validation_report.status != "passed":
                errors.append("validation report has issues and status is not passed")

    elif isinstance(decision, PartialApprovalDecision):
        approved_set = set(decision.approved_change_ids)
        rejected_set = set(decision.rejected_change_ids)
        if not approved_set:
            errors.append("partial approval must have approved_change_ids")
        if approved_set - all_ids:
            errors.append("partial approval references ids not in plan")
        if rejected_set - all_ids:
            errors.append("rejected ids not in plan")
        if approved_set & rejected_set:
            errors.append("ids in both approved and rejected")
        if approved_set | rejected_set != all_ids:
            errors.append("partial approval must cover all plan change_ids")

    elif isinstance(decision, RejectDecision):
        pass

    approved_paths_candidate = _derive_paths(plan.changes, _decision_approved_ids(decision))
    _validate_path_coverage(decision, approved_paths_candidate, errors)

    return errors


def validate_approved_paths_against_policy(
    *, decision: ApprovalDecision, policy_denied_paths: set[str],
    approved_paths: set[str] | None = None,
    changes: list[PlannedRepositoryChange] | None = None,
) -> list[str]:
    errors: list[str] = []
    if approved_paths is not None:
        candidate = approved_paths
    elif changes is not None:
        candidate = _decision_paths(decision, changes)
    else:
        candidate = set()
    for path in candidate:
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
    changes: list[PlannedRepositoryChange] | None = None,
) -> dict[str, str]:
    if isinstance(decision, (FullApprovalDecision, PartialApprovalDecision)) and changes is not None:
        approved_set = _decision_paths(decision, changes)
    else:
        approved_set = set()
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


def _decision_plan_sha(d: ApprovalDecision) -> str:
    return d.patch_plan_sha256


def _decision_ws(d: ApprovalDecision) -> str:
    return d.workspace_id


def _decision_approved_ids(d: ApprovalDecision) -> set[str]:
    if isinstance(d, (FullApprovalDecision, PartialApprovalDecision)):
        return set(d.approved_change_ids)
    return set()


def _decision_paths(d: ApprovalDecision, changes: list[PlannedRepositoryChange]) -> set[str]:
    if isinstance(d, (FullApprovalDecision, PartialApprovalDecision)):
        return _derive_paths(changes, set(d.approved_change_ids))
    return set()


def _validate_path_coverage(decision: ApprovalDecision, derived: set[str], errors: list[str]) -> None:
    if isinstance(decision, (FullApprovalDecision, PartialApprovalDecision)):
        approved_set = set(decision.approved_change_ids)
        if approved_set:
            covered = derived
            if not covered:
                errors.append("approved_change_ids derive to empty path set — check change paths")


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
