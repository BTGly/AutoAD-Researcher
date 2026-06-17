"""Command execution for environment build steps."""

import os
import re
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.environments.result import (
    CommandExecutionOutput,
    CommandStepResult,
    ResolvedCommand,
)

CommandRunner = Callable[[ResolvedCommand], CommandExecutionOutput]

_SECRET_KEY = re.compile(r"(SECRET|TOKEN|KEY|PASSWORD|CREDENTIAL)", re.IGNORECASE)


def execute_resolved_command(
    command: ResolvedCommand,
    output_dir: Path | str,
    *,
    runner: CommandRunner | None = None,
) -> CommandStepResult:
    """Execute a command with shell=False semantics and persist logs."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    if runner is None:
        raw = _run_subprocess(command)
    else:
        raw = runner(command)
    finished_at = datetime.now(timezone.utc)

    redacted_stdout = _redact_text(command.environment, raw.stdout)
    redacted_stderr = _redact_text(command.environment, raw.stderr)

    stdout_rel = f"{command.step_id}.stdout.log"
    stderr_rel = f"{command.step_id}.stderr.log"
    (output_path / stdout_rel).write_text(redacted_stdout, encoding="utf-8")
    (output_path / stderr_rel).write_text(redacted_stderr, encoding="utf-8")

    status = _status_from_output(raw)
    failure_code = None
    failure_message = None
    if status == "timeout":
        failure_code = "ENV_COMMAND_TIMEOUT"
        failure_message = "command timed out"
    elif status == "failed":
        failure_code = "ENV_COMMAND_FAILED"
        failure_message = f"command exited with code {raw.exit_code}"

    return CommandStepResult(
        schema_version=1,
        step_id=command.step_id,
        command_sha256=canonical_sha256(command),
        status=status,
        exit_code=raw.exit_code,
        stdout_path=stdout_rel,
        stderr_path=stderr_rel,
        failure_code=failure_code,
        failure_message=failure_message,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=time.monotonic() - started_monotonic,
    )


def command_from_step(step) -> ResolvedCommand:
    """Translate a CommandStep-like object into a ResolvedCommand."""
    return ResolvedCommand(
        step_id=step.step_id,
        program=step.program,
        args=list(step.args),
        cwd=step.cwd,
        environment=dict(step.environment),
        timeout_seconds=step.timeout_seconds,
    )


def _run_subprocess(command: ResolvedCommand) -> CommandExecutionOutput:
    env = os.environ.copy()
    env.update(command.environment)
    try:
        completed = subprocess.run(
            command.argv,
            cwd=command.cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=command.timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandExecutionOutput(
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
        )
    return CommandExecutionOutput(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        timed_out=False,
    )


def _status_from_output(raw: CommandExecutionOutput):
    if raw.timed_out:
        return "timeout"
    if raw.exit_code == 0:
        return "success"
    return "failed"


def _redact_text(environment: dict[str, str], text: str) -> str:
    redacted = text
    for key, value in environment.items():
        if not value:
            continue
        if _SECRET_KEY.search(key):
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted
