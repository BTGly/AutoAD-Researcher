"""Audited argv-based process tool."""

import os
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.active_repository_context import IdentifierPattern, validate_relative_path
from autoad_researcher.tools.contracts import ToolSpec
from autoad_researcher.tools.permissions import PermissionDecisionRecord, PermissionEngine, PermissionRequest


class ProcessToolRequest(BaseModel):
    """Input for the generic argv-based process tool."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_call_id: str = Field(pattern=IdentifierPattern)
    argv: list[str] = Field(min_length=1)
    cwd: Path
    cwd_label: str
    environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(gt=0)
    stage: str = Field(pattern=IdentifierPattern)
    permission_profile: str = Field(pattern=IdentifierPattern)
    skill_sha: str | None = None
    active_source_id: str | None = Field(default=None, pattern=IdentifierPattern)

    @field_validator("argv")
    @classmethod
    def _validate_argv(cls, value: list[str]) -> list[str]:
        for arg in value:
            if not arg:
                raise ValueError("argv entries must not be empty")
            if "\x00" in arg:
                raise ValueError("argv entries must not contain NUL")
        return value

    @field_validator("cwd_label")
    @classmethod
    def _validate_cwd_label(cls, value: str) -> str:
        return validate_relative_path(value)


class ProcessToolOutput(BaseModel):
    """Captured process output."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


class ProcessToolResult(BaseModel):
    """Process tool result with permission audit."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "failed", "blocked", "timed_out"]
    permission: PermissionDecisionRecord
    output: ProcessToolOutput | None = None


def process_tool_spec() -> ToolSpec:
    """Return the generic process ToolSpec."""
    return ToolSpec(
        name="process",
        description="Run one argv-based process with shell=False.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        read_only=False,
        destructive=False,
        concurrency_safe=False,
        deferred=False,
        permission_category="process",
    )


def run_process_tool(
    request: ProcessToolRequest,
    *,
    permission_engine: PermissionEngine,
) -> ProcessToolResult:
    """Run a process after permission approval. Always uses shell=False."""
    permission = permission_engine.decide(
        PermissionRequest(
            tool_call_id=request.tool_call_id,
            tool=process_tool_spec(),
            stage=request.stage,
            permission_profile=request.permission_profile,
            arguments_redacted={"argv": request.argv, "cwd_label": request.cwd_label},
            skill_sha=request.skill_sha,
            active_source_id=request.active_source_id,
            cwd_label=request.cwd_label,
        )
    )
    if permission.permission_decision != "allow":
        return ProcessToolResult(status="blocked", permission=permission)

    try:
        completed = subprocess.run(
            request.argv,
            cwd=request.cwd,
            env={**os.environ, **request.environment},
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ProcessToolResult(
            status="timed_out",
            permission=permission,
            output=ProcessToolOutput(
                exit_code=None,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                timed_out=True,
            ),
        )

    status = "success" if completed.returncode == 0 else "failed"
    return ProcessToolResult(
        status=status,
        permission=permission,
        output=ProcessToolOutput(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            timed_out=False,
        ),
    )
