"""Step 3.6–3.7: Patch Planning, Approval & Controlled Application.

code_agent/ now covers:
  - patch_planner.py: Step 3.6 — read-only planner, maps variants to
    PlannedRepositoryChange entries using ModificationHook references.
  - patch_materializer.py: RepositoryChangePlan → PatchPayload → PatchPayloadManifest.
  - payload_validator.py: DeterministicPatchPayloadValidator.
  - conflict_analyzer.py: Multi-variant conflict detection and workspace layout.
  - planner_validator.py: Deterministic PatchPlan validation (path classification,
    hook references, protected paths).
  - approval.py: Approval protocol validation (Full/Partial/Reject discriminators,
    path derivation from change_ids, policy deny override).
  - patch_applicator.py: Step 3.7 — controlled application with path enforcement,
    atomic writes, rollback support, and local validation.
  - validation_steps/: InternalValidationStep functions (ast_parse, diff_integrity,
    path_containment, before_after_identity).
  - validation_commands.py: ExternalValidationCommand template registry (ruff).
  - worktree_manager.py: WorkspaceChangeBinding, clone_shared_changes,
    merge_workspace_manifests.
  - narrow_repo_read.py: Read-only narrow filesystem access for
    PatchMaterializer context gathering.

Pipeline:
  3.4 Transfer Design → 3.5 Experiment Planner →
  3.6 Patch Planner (read-only, writes RepositoryChangePlan) →
  3.6 Materializer (generates PatchPayload/Manifest) →
  3.6 Payload Validator (validates payload integrity) →
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
from autoad_researcher.code_agent.patch_materializer import PatchMaterializer, build_payload_manifest
from autoad_researcher.code_agent.patch_planner import PatchPlannerAgent
from autoad_researcher.code_agent.payload_validator import validate_payload_manifest
from autoad_researcher.code_agent.planner_validator import validate_repository_change_plan
from autoad_researcher.code_agent.validation_commands import (
    REGISTERED_TEMPLATES, execute_template_command, validate_command_argv,
)
from autoad_researcher.code_agent.worktree_manager import (
    build_workspace_binding, clone_shared_changes,
    merge_workspace_manifests,
)
from autoad_researcher.code_agent.narrow_repo_read import (
    NarrowRepositoryReader, iter_source_files, list_files, read_file_safe,
)

__all__ = [
    "ControlledPatchApplicator",
    "NarrowRepositoryReader",
    "PatchMaterializer",
    "PatchPlannerAgent",
    "analyze_variant_conflicts",
    "apply_workspace_layout",
    "build_payload_manifest",
    "build_workspace_binding",
    "clone_shared_changes",
    "compute_approval_effective_write_paths",
    "execute_template_command",
    "iter_source_files",
    "list_files",
    "merge_workspace_manifests",
    "read_file_safe",
    "REGISTERED_TEMPLATES",
    "validate_approved_paths_against_policy",
    "validate_approval_consistency",
    "validate_command_argv",
    "validate_payload_manifest",
    "validate_repository_change_plan",
]
