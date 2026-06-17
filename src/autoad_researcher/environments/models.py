"""Generic environment planning contracts.

These models describe what an environment planner may request. They do not
execute commands and they intentionally do not encode project-specific package
or CUDA knowledge.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


class EnvironmentTarget(BaseModel):
    """Target runtime requested by an environment plan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["python_uv_venv", "python_pip_venv", "existing_python", "conda"]
    environment_path: str | None = None
    runtime_requirements: dict[str, str] = Field(default_factory=dict)
    repository_path: str | None = None


class EvidenceReference(BaseModel):
    """Evidence used by the planner to justify a plan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_type: Literal[
        "user",
        "repository",
        "document",
        "source_code",
        "host",
        "previous_error",
    ]
    path_or_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class PlanAssumption(BaseModel):
    """Assumption made by the planner and optionally tied to a validation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    assumption_id: str = Field(pattern=Identifier)
    statement: str = Field(min_length=1)
    risk: Literal["low", "medium", "high"]
    validation_id: str | None = Field(default=None, pattern=Identifier)


class CommandStep(BaseModel):
    """Single command requested by a plan.

    The executor must still run this with shell=False. The model rejects NUL
    bytes because they make argv and environment handling ambiguous.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    step_id: str = Field(pattern=Identifier)
    program: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    cwd: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(gt=0)
    network: bool
    modifies_repository: bool
    requires_approval: bool

    @field_validator("program", "cwd")
    @classmethod
    def _reject_nul_string(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("NUL byte forbidden")
        return value

    @field_validator("args")
    @classmethod
    def _reject_nul_args(cls, value: list[str]) -> list[str]:
        for arg in value:
            if "\x00" in arg:
                raise ValueError("NUL byte forbidden in args")
        return value

    @field_validator("environment")
    @classmethod
    def _reject_nul_environment(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if "\x00" in key or "\x00" in item:
                raise ValueError("NUL byte forbidden in environment")
        return value


ValidationKind = Literal[
    "runtime_version",
    "package_inventory",
    "python_import",
    "command",
    "file_exists",
    "repository_clean",
    "gpu_available",
    "gpu_compute",
    "project_smoke",
]


class ValidationStep(BaseModel):
    """Deterministic validation requested by a plan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    validation_id: str = Field(pattern=Identifier)
    kind: ValidationKind
    parameters: dict[str, Any] = Field(default_factory=dict)
    required: bool
    timeout_seconds: int = Field(gt=0)
    network: Literal[False]


class EnvironmentPermissions(BaseModel):
    """Permission envelope for build and validation."""

    model_config = ConfigDict(extra="forbid")

    network_during_build: bool = False
    network_during_validation: Literal[False] = False
    allow_system_package_install: bool = False
    allow_repository_modification: bool = False
    allow_global_environment_mutation: bool = False
    max_revision_count: int = Field(default=2, ge=0, le=2)


class EnvironmentPlan(BaseModel):
    """Planner-to-builder contract for one environment attempt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    plan_id: str = Field(pattern=Identifier)
    run_id: str = Field(pattern=Identifier)
    revision: int = Field(ge=0)
    parent_plan_id: str | None = Field(default=None, pattern=Identifier)

    target: EnvironmentTarget
    evidence: list[EvidenceReference] = Field(min_length=1)
    assumptions: list[PlanAssumption] = Field(default_factory=list)
    build_steps: list[CommandStep] = Field(min_length=1)
    validation_steps: list[ValidationStep] = Field(min_length=1)
    permissions: EnvironmentPermissions

    created_by: Literal["llm", "fixture", "user"]

    @model_validator(mode="after")
    def _validate_revision_parent(self):
        if self.revision == 0 and self.parent_plan_id is not None:
            raise ValueError("revision 0 must not have parent_plan_id")
        if self.revision > 0 and self.parent_plan_id is None:
            raise ValueError("revised plan requires parent_plan_id")
        return self


class EnvironmentPlanRevision(BaseModel):
    """A complete replacement plan with parentage and reason preserved."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    parent_plan_id: str = Field(pattern=Identifier)
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1)
    evidence: list[EvidenceReference] = Field(min_length=1)
    replacement_plan: EnvironmentPlan

    @model_validator(mode="after")
    def _validate_replacement_parentage(self):
        if self.replacement_plan.parent_plan_id != self.parent_plan_id:
            raise ValueError("replacement_plan parent_plan_id mismatch")
        if self.replacement_plan.revision != self.revision:
            raise ValueError("replacement_plan revision mismatch")
        return self
