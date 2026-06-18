"""Approval protocol — deterministic validation of approval decisions.

Ensures:
  - approved_change_ids are authoritative (paths derived from them)
  - approved_paths cannot independently expand scope
  - approve_all maps to all non-blocked change IDs
  - reject/revise must not have approved_change_ids
  - Decision must bind to the same patch_plan_sha256 as the request
  - Deny (protected paths) can never be overridden by approval
"""

from datetime import datetime

from autoad_researcher.schemas.patch_planning import (
    ApprovalDecision,
    ApprovalRequest,
    PatchPlanValidationIssue,
    PatchPlanValidationReport,
    PlannedRepositoryChange,
    RepositoryChangePlan,
)


def validate_approval_consistency(
    *,
    request: ApprovalRequest,
    decision: ApprovalDecision,
    plan: RepositoryChangePlan,
) -> list[str]:
    """Validate an ApprovalDecision against the request and plan.

    Returns a list of error messages; empty list means valid.
    """
    errors: list[str] = []

    if request.patch_plan_sha256 != decision.approved_patch_plan_sha256:
        errors.append(
            "ApprovalDecision binds to a different patch_plan_sha256 than the ApprovalRequest"
        )

    all_change_ids = {c.change_id for c in plan.changes}
    approved_set = set(decision.approved_change_ids)
    rejected_set = set(decision.rejected_change_ids)

    if decision.decision == "approve_all":
        if approved_set - all_change_ids:
            errors.append("approve_all must not reference change_ids not in the plan")
        expected = _non_blocked_change_ids(plan)
        if approved_set != expected:
            errors.append("approve_all must include all non-blocked change_ids from plan")

    elif decision.decision == "approve_partial":
        if not approved_set:
            errors.append("approve_partial must have at least one approved_change_ids")
        if approved_set - all_change_ids:
            errors.append("approve_partial references change_ids not in the plan")
        if rejected_set - all_change_ids:
            errors.append("rejected change_ids not in the plan")
        if approved_set & rejected_set:
            errors.append("change_ids in both approved and rejected sets")

    elif decision.decision in {"reject", "revise"}:
        if approved_set:
            errors.append(f"{decision.decision} must not have approved_change_ids")

    approved_paths = set(decision.approved_paths)
    derived_paths = _derive_paths_from_changes(plan.changes, approved_set)

    if approved_paths:
        for path in approved_paths:
            if path not in derived_paths:
                errors.append(
                    f"approved_paths contains {path} which is not derived from approved_change_ids"
                )
        for dp in derived_paths:
            if dp not in approved_paths:
                errors.append(
                    f"approved_paths missing {dp} which is derived from approved_change_ids"
                )

    return errors


def validate_approved_paths_against_policy(
    *,
    decision: ApprovalDecision,
    policy_denied_paths: set[str],
) -> list[str]:
    """Check that no approved path falls in policy-denied territory.

    Policy deny always wins — user approval cannot override it.
    """
    errors: list[str] = []
    for path in decision.approved_paths:
        if path in policy_denied_paths:
            errors.append(
                f"Path {path} is policy-denied and cannot be approved by user"
            )
            continue
        parts = path.split("/")[:-1]
        while parts:
            ancestor = "/".join(parts)
            if ancestor in policy_denied_paths:
                errors.append(
                    f"Ancestor directory {ancestor} of {path} is policy-denied"
                )
                break
            parts = parts[:-1]
    return errors


def compute_approval_effective_write_paths(
    *,
    decision: ApprovalDecision,
    planned_paths: set[str],
    policy_denied_paths: set[str],
    policy_ask_paths: set[str],
) -> dict[str, str]:
    """Compute the effective write path set with layered rules.

    Returns a dict mapping path → status (allow / ask / deny).
    Default is deny.
    """
    approved_set = set(decision.approved_paths)
    result: dict[str, str] = {}

    all_candidate_paths = planned_paths

    for path in all_candidate_paths:
        if path in policy_denied_paths:
            result[path] = "deny"
        elif path not in planned_paths:
            result[path] = "deny"
        elif path not in approved_set:
            result[path] = "deny"
        elif path in policy_ask_paths:
            result[path] = "ask"
        elif path not in policy_denied_paths:
            result[path] = "allow"
        else:
            result[path] = "deny"

    return result


def _non_blocked_change_ids(plan: RepositoryChangePlan) -> set[str]:
    return {c.change_id for c in plan.changes}


def _derive_paths_from_changes(
    changes: list[PlannedRepositoryChange],
    approved_change_ids: set[str],
) -> set[str]:
    paths: set[str] = set()
    for c in changes:
        if c.change_id in approved_change_ids:
            paths.add(c.repository_path)
    return paths
