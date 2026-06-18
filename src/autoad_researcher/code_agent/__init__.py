"""Step 3.6–3.7: Patch Planning, Approval & Controlled Application.

code_agent/ now covers:
  - patch_planner.py: Step 3.6 — read-only planner, maps variants to
    PlannedRepositoryChange entries using ModificationHook references.
  - conflict_analyzer.py: Multi-variant conflict detection and workspace layout.
  - planner_validator.py: Deterministic PatchPlan validation (path classification,
    hook references, protected paths).
  - approval.py: Approval protocol validation (approve_all/partial/reject/revise,
    path derivation from change_ids, policy deny override).
  - patch_applicator.py: Step 3.7 — controlled application with path enforcement,
    atomic writes, rollback support, and local validation.

Pipeline:
  3.4 Transfer Design → 3.5 Experiment Planner →
  3.6 Patch Planner (read-only, writes RepositoryChangePlan) →
  3.7 Approval → Controlled Patch Application → Local Validation →
  eligible_for_runner_intake
"""

from autoad_researcher.code_agent.approval import (
    compute_approval_effective_write_paths,
    validate_approved_paths_against_policy,
    validate_approval_consistency,
)
from autoad_researcher.code_agent.conflict_analyzer import analyze_variant_conflicts, apply_workspace_layout
from autoad_researcher.code_agent.patch_applicator import ControlledPatchApplicator
from autoad_researcher.code_agent.patch_planner import PatchPlannerAgent
from autoad_researcher.code_agent.planner_validator import validate_repository_change_plan

__all__ = [
    "ControlledPatchApplicator",
    "PatchPlannerAgent",
    "analyze_variant_conflicts",
    "apply_workspace_layout",
    "compute_approval_effective_write_paths",
    "validate_approved_paths_against_policy",
    "validate_approval_consistency",
    "validate_repository_change_plan",
]
