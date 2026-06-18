"""Step 3.6–3.7: Patch Planning, Approval & Controlled Application."""

import base64
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern, validate_workspace_path
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.clarification import ArtifactReference
from autoad_researcher.schemas.transfer_design import InterfaceContractDelta

_CHANGE_KIND_VALUES = Literal["create", "modify", "delete", "rename", "configuration_only", "test_only"]
_TARGET_MODE_VALUES = Literal["existing_target", "new_target"]
_CHECK_KIND_VALUES = Literal["syntax", "format", "static", "type", "import", "unit_test"]
_ORDERED_LIST_NAMES = {"changes", "validation_commands", "workspace_plans", "patch_payloads"}


class SymbolContractDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module_path: str = Field(min_length=1)
    symbol_name: str = Field(min_length=1)
    current_responsibility: str | None = None
    planned_responsibility: str | None = None


class PlannedDependencyChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dependency_change_id: str = Field(pattern=IdentifierPattern)
    kind: Literal["add", "remove", "upgrade", "downgrade", "pin"]
    package_name: str = Field(min_length=1)
    before_version: str | None = None
    after_version: str | None = None
    reason: str = Field(min_length=1)
    variant_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class PlannedConfigurationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_change_id: str = Field(pattern=IdentifierPattern)
    config_path: str = Field(min_length=1)
    config_key: str = Field(min_length=1)
    before_value: str | None = None
    after_value: str | None = None
    reason: str = Field(min_length=1)
    variant_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class PlannedTestChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    test_change_id: str = Field(pattern=IdentifierPattern)
    test_path: str = Field(min_length=1)
    test_kind: Literal["add", "modify"]
    target_change_ids: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class PatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload_id: str = Field(pattern=IdentifierPattern)
    change_id: str = Field(pattern=IdentifierPattern)
    payload_kind: Literal["unified_diff", "full_after_content"]
    before_sha256: str | None = None
    target_path: str = Field(min_length=1)
    payload_artifact_ref: str = Field(min_length=1)
    payload_sha256: str = Field(pattern=Sha256Pattern)


class PlannedRepositoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(pattern=IdentifierPattern)
    workspace_id: str = Field(pattern=IdentifierPattern)
    change_kind: _CHANGE_KIND_VALUES
    target_mode: _TARGET_MODE_VALUES
    hook_id: str | None = None
    existing_symbol_id: str | None = None
    proposed_symbol: str | None = None
    payload_id: str | None = None
    repository_path: str
    variant_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    symbol_delta: SymbolContractDelta | None = None
    interface_delta: InterfaceContractDelta | None = None
    risk_category: Literal["low", "medium", "high"] = "medium"
    rename_target_path: str | None = None

    @model_validator(mode="after")
    def _validate_repository_path(self):
        try: validate_workspace_path(self.repository_path)
        except ValueError as exc: raise ValueError(f"change {self.change_id}: invalid repository_path: {exc}")
        return self

    @model_validator(mode="after")
    def _validate_target_mode_and_hook(self):
        if self.target_mode == "existing_target":
            if not self.hook_id and not self.existing_symbol_id:
                raise ValueError(f"change {self.change_id}: existing_target requires hook_id or existing_symbol_id")
            if self.proposed_symbol is not None:
                raise ValueError(f"change {self.change_id}: existing_target must not set proposed_symbol")
        elif self.target_mode == "new_target":
            if self.hook_id is not None:
                raise ValueError(f"change {self.change_id}: new_target must not set hook_id")
            if not self.proposed_symbol:
                raise ValueError(f"change {self.change_id}: new_target requires proposed_symbol")
        return self

    @model_validator(mode="after")
    def _validate_change_kind_and_target_mode(self):
        kind, tm = self.change_kind, self.target_mode
        if kind == "create" and tm != "new_target":
            raise ValueError(f"change {self.change_id}: create requires target_mode=new_target")
        if kind in {"delete", "rename"} and tm != "existing_target":
            raise ValueError(f"change {self.change_id}: {kind} requires target_mode=existing_target")
        if kind == "modify" and tm != "existing_target":
            raise ValueError(f"change {self.change_id}: modify requires target_mode=existing_target")
        if kind == "rename" and not self.rename_target_path:
            raise ValueError(f"change {self.change_id}: rename requires rename_target_path")
        return self

    @model_validator(mode="after")
    def _validate_rename_target_path(self):
        if self.rename_target_path:
            try: validate_workspace_path(self.rename_target_path)
            except ValueError as exc: raise ValueError(f"change {self.change_id}: invalid rename_target_path: {exc}")
        return self


_ISOLATION_MODE_VALUES = Literal["shared_workspace", "configuration_switched", "separate_worktree"]


class VariantWorkspacePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(pattern=IdentifierPattern)
    variant_ids: list[str] = Field(default_factory=list)
    isolation_mode: _ISOLATION_MODE_VALUES
    base_repository_source_id: str = Field(min_length=1)
    base_commit: str = Field(min_length=1)
    branch_name: str | None = None
    worktree_logical_name: str | None = None
    runtime_worktree_path: str | None = None
    planned_change_ids: list[str] = Field(default_factory=list)
    conflict_group_ids: list[str] = Field(default_factory=list)


class PatchConflictGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conflict_group_id: str = Field(pattern=IdentifierPattern)
    target_path: str = Field(min_length=1)
    target_symbols: list[str] = Field(default_factory=list)
    competing_change_ids: list[str] = Field(default_factory=list)
    competing_variant_ids: list[str] = Field(default_factory=list)
    kind: Literal["no_conflict", "parameterizable", "mutually_exclusive", "path_overlap"]
    description: str = Field(min_length=1)
    resolution_change_id: str | None = None


class PatchConflictAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    workspace_plans: list[VariantWorkspacePlan] = Field(default_factory=list)
    conflict_groups: list[PatchConflictGroup] = Field(default_factory=list)
    overall_status: Literal["clean", "parameterizable_conflicts", "worktree_split_required", "incompatible"]
    recommendation: str = Field(min_length=1)


class RepositoryChangePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=IdentifierPattern)
    patch_plan_id: str = Field(pattern=IdentifierPattern)
    repository_source_id: str = Field(min_length=1)
    repository_commit: str = Field(min_length=1)
    repository_fingerprint: str = Field(min_length=1)
    selected_variant_ids: list[str] = Field(default_factory=list)
    idea_id: str = Field(pattern=IdentifierPattern)
    changes: list[PlannedRepositoryChange] = Field(default_factory=list)
    patch_payloads: list[PatchPayload] = Field(default_factory=list)
    dependency_changes: list[PlannedDependencyChange] = Field(default_factory=list)
    configuration_changes: list[PlannedConfigurationChange] = Field(default_factory=list)
    test_changes: list[PlannedTestChange] = Field(default_factory=list)
    workspace_plans: list[VariantWorkspacePlan] = Field(default_factory=list)
    conflict_analysis_id: str | None = None
    plan_sha256: str = Field(pattern=Sha256Pattern)


def compute_canonical_plan_sha256(plan: "RepositoryChangePlan") -> str:
    data = {}
    for field_name in plan.__class__.model_fields:
        if field_name == "plan_sha256":
            continue
        value = getattr(plan, field_name, None)
        data[field_name] = _serializable(value, field_name in _ORDERED_LIST_NAMES)
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    import hashlib
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _serializable(value: Any, preserve_order: bool = False) -> Any:
    if isinstance(value, BaseModel):
        obj = value.model_dump(mode="json", exclude_none=True)
        return {k: _serializable(v, k in _ORDERED_LIST_NAMES) for k, v in sorted(obj.items())}
    if isinstance(value, list):
        if preserve_order:
            return [_serializable(v) for v in value]
        return [_serializable(v) for v in sorted(value, key=lambda x: str(x))]
    if isinstance(value, dict):
        return {k: _serializable(v) for k, v in sorted(value.items())}
    return value


class PatchPlanValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(pattern=IdentifierPattern)
    category: Literal[
        "schema_violation", "path_classification_violation", "hook_reference_broken",
        "symbol_table_conflict", "variant_reference_missing", "protected_path_violation",
        "evidence_missing", "policy_violation",
    ]
    description: str = Field(min_length=1)
    artifact_ids: list[str] = Field(default_factory=list)
    affected_change_ids: list[str] = Field(default_factory=list)
    resolution: Literal["repair_change", "return_to_3_1", "blocked"]


class PatchPlanValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    plan_sha256: str = Field(pattern=Sha256Pattern)
    status: Literal["passed", "failed"]
    issues: list[PatchPlanValidationIssue] = Field(default_factory=list)
    validated_at: datetime


class SymbolQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol_name: str = Field(min_length=1)
    container_path: str = Field(min_length=1)
    query_reason: str = Field(min_length=1)


class NarrowRepositoryReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_source_id: str = Field(min_length=1)
    repository_commit: str = Field(min_length=1)
    allowed_paths: list[str] = Field(default_factory=list)
    requested_paths: list[str] = Field(default_factory=list)
    requested_symbols: list[SymbolQuery] = Field(default_factory=list)
    max_files: int = 20; max_bytes: int = 524288; max_symbol_searches: int = 10
    purpose: Literal["patch_planning"] = "patch_planning"
    originating_variant_ids: list[str] = Field(default_factory=list)


class RepositoryScopeExpansionRequired(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["return_to_3_1"] = "return_to_3_1"
    cause_variant_ids: list[str] = Field(default_factory=list)
    cause_change_ids: list[str] = Field(default_factory=list)
    cause_hook_ids: list[str] = Field(default_factory=list)
    missing_paths: list[str] = Field(default_factory=list)
    missing_symbols: list[SymbolQuery] = Field(default_factory=list)
    required_evidence_types: list[str] = Field(default_factory=list)
    current_repository_source_id: str = Field(min_length=1)
    current_repository_commit: str = Field(min_length=1)
    current_scope_sha256: str = Field(pattern=Sha256Pattern)
    reason: str = Field(min_length=1)
    completion_criteria: list[str] = Field(default_factory=list)


class WorkspaceApprovalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(pattern=IdentifierPattern)
    variant_ids: list[str] = Field(default_factory=list)
    planned_change_ids: list[str] = Field(default_factory=list)
    affected_paths: list[str] = Field(default_factory=list)
    dependency_change_ids: list[str] = Field(default_factory=list)
    risk_ids: list[str] = Field(default_factory=list)


class ValidationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(pattern=IdentifierPattern)
    label: str = Field(min_length=1)
    check_kind: _CHECK_KIND_VALUES
    required: bool = False
    argv: list[str] = Field(min_length=1)
    expected_exit_code: int = 0
    timeout_seconds: int = 120
    working_directory: str | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_request_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    repository_before_fingerprint: str = Field(min_length=1)
    selected_variant_ids: list[str] = Field(default_factory=list)
    overall_risk_level: Literal["low", "medium", "high"] = "medium"
    workspace_summaries: list[WorkspaceApprovalSummary] = Field(default_factory=list)
    dependency_changes_summary: list[PlannedDependencyChange] = Field(default_factory=list)
    validation_commands: list[ValidationCommand] = Field(default_factory=list)
    rollback_plan_sha256: str | None = None
    created_at: datetime


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str = Field(pattern=IdentifierPattern)
    decision: Literal["approve_all", "approve_partial", "revise", "reject"]
    approved_patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    approved_change_ids: list[str] = Field(default_factory=list)
    rejected_change_ids: list[str] = Field(default_factory=list)
    approved_dependency_change_ids: list[str] = Field(default_factory=list)
    approved_validation_command_ids: list[str] = Field(default_factory=list)
    approved_ask_paths: list[str] = Field(default_factory=list)
    approved_paths: list[str] = Field(default_factory=list)
    user_evidence_id: str = Field(pattern=IdentifierPattern)
    decided_at: datetime

    @model_validator(mode="after")
    def _approve_requires_changes(self):
        if self.decision in {"approve_all", "approve_partial"}:
            if not self.approved_change_ids:
                raise ValueError(f"decision={self.decision} requires approved_change_ids")
        return self

    @model_validator(mode="after")
    def _reject_revise_must_not_approve(self):
        if self.decision in {"reject", "revise"}:
            if self.approved_change_ids:
                raise ValueError(f"decision={self.decision} must not have approved_change_ids")
        return self


class ChangedFileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_entry_id: str = Field(pattern=IdentifierPattern)
    repository_path: str
    rename_target_path: str | None = None
    change_kind: _CHANGE_KIND_VALUES
    before_sha256: str | None = None
    after_sha256: str | None = None
    before_blob: str | None = None
    target_before_blob: str | None = None
    change_ids: list[str] = Field(default_factory=list)
    operation: Literal["written", "created", "deleted", "renamed"]
    applied_at: datetime


class PatchApplicationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    workspace_id: str = Field(pattern=IdentifierPattern)
    approved_decision_id: str = Field(min_length=1)
    repository_before_fingerprint: str = Field(min_length=1)
    repository_after_fingerprint: str = Field(min_length=1)
    attempted_change_ids: list[str] = Field(default_factory=list)
    applied_change_ids: list[str] = Field(default_factory=list)
    skipped_change_ids: list[str] = Field(default_factory=list)
    failed_changes: list[str] = Field(default_factory=list)
    changed_files: list[ChangedFileEntry] = Field(default_factory=list)
    patch_diff_sha256: str | None = None
    patch_diff_artifact_ref: str | None = None
    applied_at: datetime


class RollbackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rollback_id: str = Field(pattern=IdentifierPattern)
    manifest_id: str = Field(min_length=1)
    workspace_id: str = Field(pattern=IdentifierPattern)
    repository_before_fingerprint: str = Field(min_length=1)
    repository_after_fingerprint: str | None = None
    rollback_paths: list[str] = Field(default_factory=list)
    rollback_blobs: list[str] = Field(default_factory=list)
    rollback_target_paths: list[str] = Field(default_factory=list)
    rollback_target_blobs: list[str] = Field(default_factory=list)
    rollback_order: Literal["reverse_apply_order", "destroy_worktree"]
    rollback_strategy: str = Field(min_length=1)
    rollback_applied: bool = False
    rollback_fingerprint: str | None = None
    fingerprint_matches_before: bool | None = None
    rollback_at: datetime | None = None


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["passed", "failed", "not_run", "not_required"]
    command_id: str | None = None
    exit_code: int | None = None
    stdout_ref: str | None = None
    stderr_ref: str | None = None


class PostPatchValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    workspace_id: str = Field(pattern=IdentifierPattern)
    manifest_id: str = Field(min_length=1)
    status: Literal["patch_applied_and_local_validations_passed", "patch_applied_but_local_validation_failed"]
    syntax_check: CheckResult = Field(default_factory=lambda: CheckResult(status="not_run"))
    format_check: CheckResult = Field(default_factory=lambda: CheckResult(status="not_run"))
    static_check: CheckResult = Field(default_factory=lambda: CheckResult(status="not_run"))
    type_check: CheckResult = Field(default_factory=lambda: CheckResult(status="not_run"))
    import_check: CheckResult = Field(default_factory=lambda: CheckResult(status="not_run"))
    unit_tests: CheckResult | None = None
    issues: list[str] = Field(default_factory=list)
    validated_at: datetime

    @model_validator(mode="after")
    def _passed_requires_all_run_passed(self):
        if self.status == "patch_applied_and_local_validations_passed":
            for check in [self.syntax_check, self.format_check, self.static_check,
                          self.type_check, self.import_check]:
                if check.status not in {"passed", "not_required"}:
                    raise ValueError("status=passed requires all checks passed or not_required")
        return self


class PatchApplicationPreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preflight_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    workspace_id: str = Field(pattern=IdentifierPattern)
    plan_sha_valid: bool = False
    decision_sha_valid: bool = False
    request_sha_valid: bool = False
    repository_fingerprint_match: bool = False
    run_id_match: bool = False
    workspace_exists_in_plan: bool = False
    validation_report_valid: bool = False
    ready: bool = False
    issues: list[str] = Field(default_factory=list)


class PatchExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    preflight: PatchApplicationPreflightResult | None = None
    overall_status: Literal[
        "patch_applied", "patch_application_partial_failure",
        "patch_application_failed", "patch_applied_and_local_validations_passed",
        "patch_applied_but_local_validation_failed", "rolled_back",
        "rollback_failed",
        "replan_required", "rejected", "blocked",
    ]
    manifests: list[PatchApplicationManifest] = Field(default_factory=list)
    validation_reports: list[PostPatchValidationReport] = Field(default_factory=list)
    rollback_manifests: list[RollbackManifest] = Field(default_factory=list)
    next_stage: Literal[
        "eligible_for_runner_intake", "repair_or_rollback_pending",
        "replan_required", "rejected", "blocked",
    ]

    @model_validator(mode="after")
    def _intake_requires_validation(self):
        if self.next_stage == "eligible_for_runner_intake":
            if self.overall_status != "patch_applied_and_local_validations_passed":
                raise ValueError("intake requires overall_status=patch_applied_and_local_validations_passed")
            if not self.validation_reports:
                raise ValueError("intake requires validation_report")
        return self

    @model_validator(mode="after")
    def _failed_not_to_intake(self):
        if self.overall_status in {"patch_application_failed", "rolled_back", "replan_required"}:
            if self.next_stage == "eligible_for_runner_intake":
                raise ValueError(f"{self.overall_status} incompatible with intake")
        return self


# ---------------------------------------------------------------------------
# Step 3.7 → 3.8: Multi-workspace PatchRunnerHandoff v2
# ---------------------------------------------------------------------------


class BaselineWorkspaceRef(BaseModel):
    """Baseline workspace — repo without any variant patch applied."""
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(pattern=IdentifierPattern)
    repository_fingerprint: str = Field(min_length=1)
    repository_commit: str = Field(min_length=1)
    repository_validation_ref: ArtifactReferenceV2


class VariantWorkspaceHandoff(BaseModel):
    """A workspace with one or more variant patches applied."""
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(pattern=IdentifierPattern)
    variant_ids: list[str] = Field(min_length=1)
    repository_fingerprint: str = Field(min_length=1)
    patch_diff_sha256: str = Field(pattern=Sha256Pattern)
    local_validation_report_sha256: str = Field(pattern=Sha256Pattern)
    patch_application_manifest_ref: ArtifactReferenceV2
    post_patch_validation_report_ref: ArtifactReferenceV2


class PatchRunnerHandoff(BaseModel):
    """Structured handoff from Step 3.7 to Step 3.8.

    v2: supports multiple variant workspaces + baseline workspace.
    """
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    status: Literal["eligible_for_runner_intake"] = "eligible_for_runner_intake"
    run_id: str = Field(pattern=IdentifierPattern)
    repository_before_commit: str = Field(min_length=1)
    approved_patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    selected_variant_ids: list[str] = Field(default_factory=list)
    experiment_bundle_ref: str = Field(min_length=1)
    baseline_workspace_ref: BaselineWorkspaceRef
    variant_workspaces: list[VariantWorkspaceHandoff] = Field(default_factory=list)
    next_stage: Literal["runner_intake"] = "runner_intake"

    @model_validator(mode="after")
    def _selected_variants_match_workspaces(self):
        from itertools import chain
        selected = self.selected_variant_ids
        workspace_variant_ids = list(chain.from_iterable(
            ws.variant_ids for ws in self.variant_workspaces
        ))

        if len(selected) != len(set(selected)):
            raise ValueError("duplicate selected_variant_ids")

        workspace_id_list = [ws.workspace_id for ws in self.variant_workspaces]
        if len(workspace_id_list) != len(set(workspace_id_list)):
            raise ValueError("duplicate workspace_id in variant_workspaces")

        if len(workspace_variant_ids) != len(set(workspace_variant_ids)):
            raise ValueError("variant appears in multiple workspaces")

        if set(selected) != set(workspace_variant_ids):
            missing = set(selected) - set(workspace_variant_ids)
            extra = set(workspace_variant_ids) - set(selected)
            parts = []
            if missing:
                parts.append(f"selected variants not in any workspace: {sorted(missing)}")
            if extra:
                parts.append(f"workspace variants not selected: {sorted(extra)}")
            raise ValueError("; ".join(parts))
        return self
