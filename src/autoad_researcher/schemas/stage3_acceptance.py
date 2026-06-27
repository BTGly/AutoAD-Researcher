"""Step 3.10 acceptance schemas.

These contracts cover L1/L2 deterministic acceptance only.  L3 real task
evidence must be produced by a later GPU/provider-enabled run.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoad_researcher.paper_intelligence.ids import Sha256Pattern, validate_workspace_path


STAGE3_ACCEPTANCE_STAGE_ORDER: tuple[str, ...] = (
    "intake",
    "repository_intelligence",
    "paper_intelligence",
    "research_context",
    "transfer_design",
    "experiment_planner",
    "patch_planner",
    "patch_applicator",
    "runner_execute",
    "results_analysis",
    "final_report",
)

PENDING_L3_ARTIFACTS: tuple[str, ...] = (
    "comparison_report.md",
    "scientific_validity_report.json",
    "final_report.json",
    "demo_script.md",
    "demo_runbook.md",
)

Stage3AcceptanceStageName = Literal[
    "intake",
    "repository_intelligence",
    "paper_intelligence",
    "research_context",
    "transfer_design",
    "experiment_planner",
    "patch_planner",
    "patch_applicator",
    "runner_execute",
    "results_analysis",
    "final_report",
]
Stage3AcceptanceMode = Literal["l1-l2", "l3-preflight"]
Stage3AcceptanceStatus = Literal["passed", "blocked", "failed"]


class Stage3ProviderConfig(BaseModel):
    """Explicit provider configuration for future L3 runs."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["deepseek"] = "deepseek"
    model_id: str = Field(default="deepseek-v4-flash", min_length=1)
    base_url: str | None = None
    api_key_env: str = Field(default="DEEPSEEK_API_KEY", min_length=1)
    max_calls: int = Field(default=0, ge=0)
    structured_output_required: bool = True

    @field_validator("api_key_env")
    @classmethod
    def validate_api_key_env(cls, value: str) -> str:
        if any(ch.isspace() for ch in value):
            raise ValueError("api_key_env must not contain whitespace")
        if value.startswith(("sk-", "ghp_", "api_")):
            raise ValueError("api_key_env must be an environment variable name, not a secret value")
        return value


class Stage3AcceptanceArtifactRef(BaseModel):
    """SHA-addressed artifact reference without embedded payload data."""

    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(min_length=1)
    sha256: str = Field(pattern=Sha256Pattern)
    artifact_type: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_workspace_path(value)


class Stage3AcceptanceStageRecord(BaseModel):
    """Acceptance status for one Stage 3 pipeline stage."""

    model_config = ConfigDict(extra="forbid")

    stage: Stage3AcceptanceStageName
    status: Stage3AcceptanceStatus
    handoff_sha256: str | None = Field(default=None, pattern=Sha256Pattern)
    artifacts: list[Stage3AcceptanceArtifactRef] = Field(default_factory=list)
    blocked_reason: str | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> "Stage3AcceptanceStageRecord":
        if self.status == "passed":
            if not self.artifacts:
                raise ValueError("passed stage must include at least one artifact")
            if self.handoff_sha256 is None:
                raise ValueError("passed stage must include handoff_sha256")
            if self.blocked_reason is not None:
                raise ValueError("passed stage must not include blocked_reason")
        if self.status in {"blocked", "failed"} and not self.blocked_reason:
            raise ValueError("blocked or failed stage must include blocked_reason")
        return self


class ArtifactChainBinding(BaseModel):
    """SHA binding between adjacent stage handoffs."""

    model_config = ConfigDict(extra="forbid")

    upstream_stage: Stage3AcceptanceStageName
    downstream_stage: Stage3AcceptanceStageName
    upstream_handoff_sha256: str = Field(pattern=Sha256Pattern)
    downstream_input_ref_sha256: str = Field(pattern=Sha256Pattern)
    match: bool

    @model_validator(mode="after")
    def validate_match(self) -> "ArtifactChainBinding":
        expected = self.upstream_handoff_sha256 == self.downstream_input_ref_sha256
        if self.match != expected:
            raise ValueError("match must equal SHA equality")
        return self


class ArtifactChainValidationReport(BaseModel):
    """Validation result for the Stage 3 artifact chain."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    bindings: list[ArtifactChainBinding] = Field(default_factory=list)
    all_match: bool

    @model_validator(mode="after")
    def validate_all_match(self) -> "ArtifactChainValidationReport":
        expected = all(binding.match for binding in self.bindings)
        if self.all_match != expected:
            raise ValueError("all_match must equal all binding match values")
        return self


class SecurityGateReport(BaseModel):
    """L1/L2 security gate summary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    process_tool_checked: bool
    filesystem_scope_checked: bool
    permission_engine_checked: bool
    l3_real_execution_allowed: bool = False
    status: Literal["passed", "blocked"]

    @model_validator(mode="after")
    def validate_security_status(self) -> "SecurityGateReport":
        import os
        real_exec_override = bool(os.environ.get("AUTOAD_L3_REAL_EXECUTION_ALLOWED"))
        checks_passed = (
            self.process_tool_checked
            and self.filesystem_scope_checked
            and self.permission_engine_checked
            and (not self.l3_real_execution_allowed or real_exec_override)
        )
        expected = "passed" if checks_passed else "blocked"
        if self.status != expected:
            raise ValueError("status must reflect L1/L2 security checks")
        return self


class Stage3AcceptanceManifest(BaseModel):
    """Top-level Stage 3 L1/L2 acceptance manifest."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    mode: Stage3AcceptanceMode
    stages: list[Stage3AcceptanceStageRecord]
    final_handoff_sha256: str | None = Field(default=None, pattern=Sha256Pattern)
    sha_chain_closed: bool
    all_stages_completed: bool
    failed_stage: Stage3AcceptanceStageName | None = None

    @model_validator(mode="after")
    def validate_stage_order(self) -> "Stage3AcceptanceManifest":
        observed = [stage.stage for stage in self.stages]
        expected = list(STAGE3_ACCEPTANCE_STAGE_ORDER)
        if observed != expected:
            raise ValueError("stages must match canonical Stage 3 order without duplicates")

        completed = all(stage.status == "passed" for stage in self.stages)
        if self.all_stages_completed != completed:
            raise ValueError("all_stages_completed must reflect stage statuses")

        first_non_passed = next((stage.stage for stage in self.stages if stage.status != "passed"), None)
        if self.failed_stage != first_non_passed:
            raise ValueError("failed_stage must be the first non-passed stage or None")

        expected_final = self.stages[-1].handoff_sha256 if completed else None
        if self.final_handoff_sha256 != expected_final:
            raise ValueError("final_handoff_sha256 must equal final stage handoff when complete")
        return self


class EndToEndRunReport(BaseModel):
    """Machine-readable L1/L2 end-to-end acceptance report."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    mode: Stage3AcceptanceMode
    status: Stage3AcceptanceStatus
    stage_results: list[Stage3AcceptanceStageRecord]
    failed_stage: Stage3AcceptanceStageName | None = None
    failure_reason: str | None = None
    pending_l3_artifacts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report_status(self) -> "EndToEndRunReport":
        if self.status == "passed":
            if self.failed_stage is not None or self.failure_reason is not None:
                raise ValueError("passed report must not include failed_stage or failure_reason")
        elif self.failure_reason is None:
            raise ValueError("blocked or failed report must include failure_reason")
        return self


class Stage3AcceptanceResult(BaseModel):
    """CLI/API return value for Stage 3 acceptance."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    mode: Stage3AcceptanceMode
    status: Stage3AcceptanceStatus
    artifact_dir: str = Field(min_length=1)
    artifacts: dict[str, str] = Field(default_factory=dict)
    failed_stage: Stage3AcceptanceStageName | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_result_status(self) -> "Stage3AcceptanceResult":
        if self.status == "passed":
            if self.failed_stage is not None:
                raise ValueError("passed result must not include failed_stage")
            if self.failure_reason is not None:
                raise ValueError("passed result must not include failure_reason")
        elif self.failure_reason is None:
            raise ValueError("blocked or failed result must include failure_reason")
        return self


class Stage3AcceptanceRequest(BaseModel):
    """Request for deterministic Stage 3 acceptance orchestration."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    runs_root: str = "runs"
    mode: Stage3AcceptanceMode = "l1-l2"
    provider_config: Stage3ProviderConfig = Field(default_factory=Stage3ProviderConfig)
    required_artifact_paths: dict[Stage3AcceptanceStageName, list[str]] = Field(default_factory=dict)
    expected_chain_bindings: list[ArtifactChainBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_required_paths(self) -> "Stage3AcceptanceRequest":
        for paths in self.required_artifact_paths.values():
            if not paths:
                raise ValueError("required_artifact_paths entries must not be empty")
            for path in paths:
                validate_workspace_path(path)
        return self
