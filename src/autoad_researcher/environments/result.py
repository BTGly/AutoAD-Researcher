"""Environment build result contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResolvedCommand(BaseModel):
    """A command ready for shell=False execution."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    step_id: str
    program: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    cwd: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(gt=0)

    @property
    def argv(self) -> list[str]:
        return [self.program, *self.args]


class CommandExecutionOutput(BaseModel):
    """Raw output returned by an injected runner or subprocess wrapper."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class CommandStepResult(BaseModel):
    """Evidence for one command step."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    step_id: str
    command_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["success", "failed", "timeout"]
    exit_code: int | None
    stdout_path: str
    stderr_path: str
    failure_code: str | None = None
    failure_message: str | None = None
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(ge=0)


class EnvironmentBuildResult(BaseModel):
    """Summary for an environment build attempt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    run_id: str
    plan_id: str
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["success", "failed"]
    adapter: str
    environment_path: str | None
    step_results: list[CommandStepResult]
    snapshot_path: str | None = None
    validation_report_path: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    started_at: datetime
    finished_at: datetime
