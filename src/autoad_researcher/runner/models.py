"""Generic controlled experiment runner contracts."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
Sha256Hex = r"^[0-9a-f]{64}$"


class ExperimentCommandPlan(BaseModel):
    """Immutable command plan for one experiment attempt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    command_id: str = Field(pattern=Identifier)
    program: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    cwd: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(gt=0)
    network: Literal[False]
    expected_outputs: list[str] = Field(min_length=1)

    @field_validator("cwd")
    @classmethod
    def _validate_cwd(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("expected_outputs")
    @classmethod
    def _validate_outputs(cls, value: list[str]) -> list[str]:
        for path in value:
            _validate_relative_path(path)
        return value

    @model_validator(mode="after")
    def _reject_nul_and_shell_tokens(self):
        values = [self.program, self.cwd, *self.args, *self.environment.keys(), *self.environment.values()]
        for value in values:
            if "\x00" in value:
                raise ValueError("NUL byte forbidden")
        for arg in self.args:
            if any(token in arg for token in ["|", ">", "<", "&&", "||", ";", "`", "$("]):
                raise ValueError("shell metacharacter forbidden in args")
        return self


class ExperimentInputRefs(BaseModel):
    """SHA/fingerprint references required before execution."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repository_fingerprint: str = Field(min_length=1)
    environment_sha256: str = Field(pattern=Sha256Hex)
    dataset_manifest_sha256: str = Field(pattern=Sha256Hex)
    asset_manifest_sha256: str = Field(pattern=Sha256Hex)
    command_sha256: str = Field(pattern=Sha256Hex)


class OutputManifestEntry(BaseModel):
    """One produced output file."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str
    sha256: str = Field(pattern=Sha256Hex)
    size_bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _validate_relative_path(value)


class OutputManifest(BaseModel):
    """Manifest of produced attempt outputs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    outputs: list[OutputManifestEntry]
    manifest_sha256: str = Field(pattern=Sha256Hex)


class ExperimentExecutionResult(BaseModel):
    """Execution outcome for one immutable attempt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    run_id: str = Field(pattern=Identifier)
    attempt: str = Field(pattern=Identifier)
    command_id: str = Field(pattern=Identifier)
    command_sha256: str = Field(pattern=Sha256Hex)
    status: Literal[
        "preflight_failed",
        "execution_failed",
        "metric_parse_failed",
        "invalid_repository_mutation",
        "success",
    ]
    exit_code: int | None = None
    timed_out: bool = False
    stdout_path: str
    stderr_path: str
    output_manifest_path: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None

    @model_validator(mode="after")
    def _validate_status_fields(self):
        if self.status == "success":
            if self.exit_code != 0 or self.timed_out:
                raise ValueError("success requires exit_code=0 and timed_out=false")
            if self.output_manifest_path is None:
                raise ValueError("success requires output_manifest_path")
            if self.failure_code is not None:
                raise ValueError("success must not include failure_code")
        else:
            if self.failure_code is None or self.failure_message is None:
                raise ValueError("failed execution result requires failure fields")
        return self


def _validate_relative_path(value: str) -> str:
    if "\\" in value:
        raise ValueError(f"backslash forbidden in path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError(f"absolute path forbidden: {value!r}")
    if value in {"", "."}:
        raise ValueError("path must not be empty or '.'")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"parent traversal forbidden: {value!r}")
    return value
