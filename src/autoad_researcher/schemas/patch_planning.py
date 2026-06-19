"""Step 3.6–3.7: Patch Planning, Approval & Controlled Application — v1.5.8 schema."""

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern, validate_workspace_path
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.clarification import ArtifactReference
from autoad_researcher.schemas.transfer_design import InterfaceContractDelta

_OPERATION_KIND_VALUES = Literal["create", "modify", "delete", "rename"]
_CHANGE_ROLE_VALUES = Literal["implementation", "configuration", "test"]
_TARGET_MODE_VALUES = Literal["existing_target", "new_target"]
_COLLISION_POLICY_VALUES = Literal["must_not_exist", "replace_existing"]


# ── Self-referencing canonical SHA exclusion ──────────────────────────

CANONICAL_HASH_EXCLUDED_FIELDS: dict[type[BaseModel], set[str]] = {
    # populated after class definitions below
}


def _model_canonical_excluded(model_cls: type[BaseModel]) -> set[str]:
    return CANONICAL_HASH_EXCLUDED_FIELDS.get(model_cls, set())


# ── Normalisation helpers ─────────────────────────────────────────────

def _normalize(value: Any) -> Any:
    """Recursively normalise a Python value for canonical serialisation."""
    if isinstance(value, BaseModel):
        d = value.model_dump(exclude_none=True, mode="python")
        excluded = _model_canonical_excluded(type(value))
        return {k: _normalize(v) for k, v in sorted(d.items()) if k not in excluded}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime is forbidden in canonical SHA")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    return value


def canonical_sha(model: BaseModel) -> str:
    """Compute self-referencing-safe canonical SHA-256 for any BaseModel.

    Excludes the model's self-hash field(s) per CANONICAL_HASH_EXCLUDED_FIELDS,
    normalises datetimes to UTC Z, preserves list order, and uses compact JSON.
    """
    import hashlib

    d = _normalize(model)
    raw = json.dumps(
        d,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_canonical_plan_sha256(plan: "RepositoryChangePlan") -> str:
    return canonical_sha(plan)


# ── Core data models ──────────────────────────────────────────────────

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
    payload_media_type: Literal["text/x-diff", "application/octet-stream"] = "text/x-diff"
    payload_size_bytes: int = 0
    before_sha256: str | None = None
    target_before_sha256: str | None = None
    target_path: str = Field(min_length=1)
    payload_artifact_id: str = Field(min_length=1)
    payload_sha256: str = Field(pattern=Sha256Pattern)


class PlannedRepositoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(pattern=IdentifierPattern)
    workspace_id: str = Field(pattern=IdentifierPattern)
    operation_kind: _OPERATION_KIND_VALUES
    change_role: _CHANGE_ROLE_VALUES = "implementation"
    target_mode: _TARGET_MODE_VALUES
    hook_id: str | None = None
    existing_symbol_id: str | None = None
    proposed_symbol: str | None = None
    payload_id: str | None = None
    repository_path: str
    variant_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    symbol_delta: SymbolContractDelta | None = None
    interface_delta: InterfaceContractDelta | None = None
    rename_target_path: str | None = None
    target_collision_policy: _COLLISION_POLICY_VALUES = "must_not_exist"
    target_before_sha256: str | None = None

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
    def _validate_operation_kind_and_target_mode(self):
        ok, tm = self.operation_kind, self.target_mode
        if ok == "create" and tm != "new_target":
            raise ValueError(f"change {self.change_id}: create requires target_mode=new_target")
        if ok in {"delete", "rename"} and tm != "existing_target":
            raise ValueError(f"change {self.change_id}: {ok} requires target_mode=existing_target")
        if ok == "modify" and tm != "existing_target":
            raise ValueError(f"change {self.change_id}: modify requires target_mode=existing_target")
        if ok == "rename" and not self.rename_target_path:
            raise ValueError(f"change {self.change_id}: rename requires rename_target_path")
        return self

    @model_validator(mode="after")
    def _validate_collision_policy(self):
        if self.target_collision_policy == "replace_existing":
            if not self.target_before_sha256:
                raise ValueError(f"change {self.change_id}: replace_existing requires target_before_sha256")
        return self

    @model_validator(mode="after")
    def _validate_rename_target_path(self):
        if self.rename_target_path:
            try: validate_workspace_path(self.rename_target_path)
            except ValueError as exc: raise ValueError(f"change {self.change_id}: invalid rename_target_path: {exc}")
        return self


# ── Workspace / conflict models ───────────────────────────────────────

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


# ── Plan ──────────────────────────────────────────────────────────────

class RepositoryChangePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    run_id: str = Field(pattern=IdentifierPattern)
    patch_plan_id: str = Field(pattern=IdentifierPattern)
    repository_source_id: str = Field(min_length=1)
    repository_commit: str = Field(min_length=1)
    repository_fingerprint: str = Field(min_length=1)
    selected_variant_ids: list[str] = Field(default_factory=list)
    idea_id: str = Field(pattern=IdentifierPattern)
    changes: list[PlannedRepositoryChange] = Field(default_factory=list)
    dependency_changes: list[PlannedDependencyChange] = Field(default_factory=list)
    configuration_changes: list[PlannedConfigurationChange] = Field(default_factory=list)
    test_changes: list[PlannedTestChange] = Field(default_factory=list)
    workspace_plans: list[VariantWorkspacePlan] = Field(default_factory=list)
    conflict_analysis_id: str | None = None
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)


# ── Validation reports ────────────────────────────────────────────────

class PatchPlanValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(pattern=IdentifierPattern)
    category: Literal[
        "schema_violation", "path_classification_violation", "hook_reference_broken",
        "symbol_table_conflict", "variant_reference_missing", "protected_path_violation",
        "evidence_missing", "policy_violation", "target_before_sha_mismatch",
    ]
    description: str = Field(min_length=1)
    resolution: Literal["repair_change", "return_to_3_1", "blocked"]


class PatchPlanValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    status: Literal["passed", "failed"]
    issues: list[PatchPlanValidationIssue] = Field(default_factory=list)
    validated_at: datetime


class PatchPayloadManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    workspace_id: str = Field(pattern=IdentifierPattern)
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    payloads: list[PatchPayload] = Field(default_factory=list)
    proposed_diff_artifact_id: str = Field(min_length=1)
    proposed_diff_sha256: str = Field(pattern=Sha256Pattern)
    manifest_sha256: str = Field(pattern=Sha256Pattern)


class PatchPayloadValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(pattern=IdentifierPattern)
    payload_id: str | None = None
    change_id: str | None = None
    category: Literal[
        "payload_sha_mismatch", "before_sha_mismatch",
        "target_before_sha_mismatch",
        "undeclared_path", "undeclared_file_creation",
        "file_mode_change", "symlink_change", "submodule_change",
        "unsupported_payload_kind",
    ]
    description: str = Field(min_length=1)
    resolution: Literal["regenerate", "blocked"]


class PatchPayloadValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str = Field(pattern=IdentifierPattern)
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    payload_manifest_sha256: str = Field(pattern=Sha256Pattern)
    status: Literal["passed", "failed"]
    issues: list[PatchPayloadValidationIssue] = Field(default_factory=list)
    validated_at: datetime


# ── Internal / external validation ────────────────────────────────────

class InternalValidationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: Literal[
        "ast_parse",
        "before_after_identity",
        "diff_integrity",
        "import_declaration_scan",
        "path_containment",
    ]
    target_artifact_ids: list[str] = Field(default_factory=list)
    required: bool = True


class ExternalValidationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(pattern=IdentifierPattern)
    template_id: Literal["ruff_check_no_fix", "ruff_format_check"]
    resolved_argv: list[str] = Field(min_length=1)
    working_directory: str = Field(min_length=1)
    required: bool = True


# ── Bundle ────────────────────────────────────────────────────────────

class ApprovalPatchBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle_id: str = Field(pattern=IdentifierPattern)
    approval_request_id: str = Field(min_length=1)
    created_at: datetime
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    workspace_id: str = Field(pattern=IdentifierPattern)
    approved_change_ids: list[str] = Field(default_factory=list)
    approved_payload_ids: list[str] = Field(default_factory=list)
    approved_diff_artifact_id: str = Field(min_length=1)
    approved_diff_sha256: str = Field(pattern=Sha256Pattern)
    payload_manifest_sha256: str = Field(pattern=Sha256Pattern)
    bundle_sha256: str = Field(pattern=Sha256Pattern)


# ── Narrow repository read ────────────────────────────────────────────

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
    missing_symbols: list[str] = Field(default_factory=list)
    required_evidence_types: list[str] = Field(default_factory=list)
    current_repository_source_id: str = Field(min_length=1)
    current_repository_commit: str = Field(min_length=1)
    current_scope_sha256: str = Field(pattern=Sha256Pattern)
    reason: str = Field(min_length=1)
    completion_criteria: list[str] = Field(default_factory=list)


# ── Workspace summary ─────────────────────────────────────────────────

class WorkspaceApprovalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(pattern=IdentifierPattern)
    variant_ids: list[str] = Field(default_factory=list)
    planned_change_ids: list[str] = Field(default_factory=list)
    affected_paths: list[str] = Field(default_factory=list)
    dependency_change_ids: list[str] = Field(default_factory=list)
    risk_ids: list[str] = Field(default_factory=list)


# ── Approval request ──────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_request_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    workspace_id: str = Field(pattern=IdentifierPattern)
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    patch_payload_manifest_sha256: str = Field(pattern=Sha256Pattern)
    proposed_patch_diff_sha256: str = Field(pattern=Sha256Pattern)
    patch_payload_validation_report_sha256: str = Field(pattern=Sha256Pattern)
    patch_plan_validation_report_sha256: str = Field(pattern=Sha256Pattern)
    repository_before_fingerprint: str = Field(min_length=1)
    selected_variant_ids: list[str] = Field(default_factory=list)
    overall_risk_level: Literal["low", "medium", "high"] = "medium"
    workspace_summary: WorkspaceApprovalSummary
    internal_validation_steps: list[InternalValidationStep] = Field(default_factory=list)
    external_validation_commands: list[ExternalValidationCommand] = Field(default_factory=list)
    approval_request_sha256: str = Field(pattern=Sha256Pattern)
    created_at: datetime


# ── Approval decisions ────────────────────────────────────────────────

class FullApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str = Field(pattern=IdentifierPattern)
    decision: Literal["approve_all"] = "approve_all"
    approval_request_id: str = Field(min_length=1)
    approved_request_sha256: str = Field(pattern=Sha256Pattern)
    workspace_id: str = Field(pattern=IdentifierPattern)
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    payload_manifest_sha256: str = Field(pattern=Sha256Pattern)
    approved_diff_sha256: str = Field(pattern=Sha256Pattern)
    approved_change_ids: list[str] = Field(default_factory=list)
    approved_paths: list[str] = Field(default_factory=list)
    approved_ask_paths: list[str] = Field(default_factory=list)
    approved_internal_step_ids: list[str] = Field(default_factory=list)
    approved_external_command_ids: list[str] = Field(default_factory=list)
    approved_collision_change_ids: list[str] = Field(default_factory=list)
    user_evidence_id: str = Field(pattern=IdentifierPattern)
    decided_at: datetime

    @model_validator(mode="after")
    def _approve_requires_changes(self):
        if not self.approved_change_ids:
            raise ValueError("full approval requires approved_change_ids")
        return self


class PartialApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str = Field(pattern=IdentifierPattern)
    decision: Literal["approve_partial"] = "approve_partial"
    approval_request_id: str = Field(min_length=1)
    approved_request_sha256: str = Field(pattern=Sha256Pattern)
    workspace_id: str = Field(pattern=IdentifierPattern)
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    payload_manifest_sha256: str = Field(pattern=Sha256Pattern)
    approval_patch_bundle_sha256: str = Field(pattern=Sha256Pattern)
    approved_change_ids: list[str] = Field(default_factory=list)
    rejected_change_ids: list[str] = Field(default_factory=list)
    approved_paths: list[str] = Field(default_factory=list)
    approved_ask_paths: list[str] = Field(default_factory=list)
    approved_internal_step_ids: list[str] = Field(default_factory=list)
    approved_external_command_ids: list[str] = Field(default_factory=list)
    approved_collision_change_ids: list[str] = Field(default_factory=list)
    user_evidence_id: str = Field(pattern=IdentifierPattern)
    decided_at: datetime

    @model_validator(mode="after")
    def _approve_requires_changes(self):
        if not self.approved_change_ids:
            raise ValueError("partial approval requires approved_change_ids")
        return self


class RejectDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str = Field(pattern=IdentifierPattern)
    decision: Literal["reject", "revise"] = "reject"
    approval_request_id: str = Field(min_length=1)
    workspace_id: str = Field(pattern=IdentifierPattern)
    patch_plan_sha256: str = Field(pattern=Sha256Pattern)
    rejected_request_sha256: str = Field(pattern=Sha256Pattern)
    reason: str | None = None
    user_evidence_id: str = Field(pattern=IdentifierPattern)
    decided_at: datetime


ApprovalDecision = FullApprovalDecision | PartialApprovalDecision | RejectDecision


# ── Application models ────────────────────────────────────────────────

class ChangedFileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_entry_id: str = Field(pattern=IdentifierPattern)
    repository_path: str
    rename_target_path: str | None = None
    operation_kind: _OPERATION_KIND_VALUES
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
    patch_diff_artifact_id: str | None = None
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


# ── Step 3.7 → 3.8 handoff ────────────────────────────────────────────

class BaselineWorkspaceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(pattern=IdentifierPattern)
    repository_fingerprint: str = Field(min_length=1)
    repository_commit: str = Field(min_length=1)
    repository_validation_ref: ArtifactReferenceV2


class VariantWorkspaceHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(pattern=IdentifierPattern)
    variant_ids: list[str] = Field(min_length=1)
    repository_fingerprint: str = Field(min_length=1)
    patch_diff_sha256: str = Field(pattern=Sha256Pattern)
    local_validation_report_sha256: str = Field(pattern=Sha256Pattern)
    patch_application_manifest_ref: ArtifactReferenceV2
    post_patch_validation_report_ref: ArtifactReferenceV2


class PatchRunnerHandoff(BaseModel):
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


# ── Register self-hash exclusions after all models are defined ────────

CANONICAL_HASH_EXCLUDED_FIELDS.update({
    ApprovalRequest: {"approval_request_sha256"},
    PatchPayloadManifest: {"manifest_sha256"},
    ApprovalPatchBundle: {"bundle_sha256"},
    RepositoryChangePlan: {"patch_plan_sha256"},
})
