"""ExternalValidationCommand templates — registered validation commands.

Contracts:
  - template_id="ruff_check_no_fix"
    → resolved_argv must be exactly ["ruff", "check", "--no-fix", "--no-unsafe-fixes"]
  - template_id="ruff_format_check"
    → resolved_argv must be exactly ["ruff", "format", "--check"]

New template_id values must be registered here before use.
"""

import subprocess
from pathlib import Path
from typing import Optional

from autoad_researcher.schemas.patch_planning import CheckResult, ExternalValidationCommand


REGISTERED_TEMPLATES: dict[str, list[str]] = {
    "ruff_check_no_fix": ["ruff", "check", "--no-fix", "--no-unsafe-fixes"],
    "ruff_format_check": ["ruff", "format", "--check"],
}


def validate_command_argv(command: ExternalValidationCommand) -> Optional[str]:
    """Validate that a command's resolved_argv matches its registered template.

    Returns error message or None if valid.
    """
    template = REGISTERED_TEMPLATES.get(command.template_id)
    if template is None:
        return f"unknown template_id: {command.template_id}"

    if command.resolved_argv != template:
        return (f"template {command.template_id} argv mismatch: "
                f"expected {template}, got {command.resolved_argv}")

    return None


def execute_template_command(
    command: ExternalValidationCommand,
    *,
    repository_root: Optional[Path] = None,
    timeout: int = 120,
) -> CheckResult:
    """Execute a validated template command and return CheckResult.

    Validates argv against registered template before execution.
    """
    err = validate_command_argv(command)
    if err is not None:
        return CheckResult(
            status="failed",
            command_id=command.command_id,
            exit_code=None,
            stderr_ref=err,
        )

    cwd = command.working_directory or str(repository_root) if repository_root else None
    try:
        proc = subprocess.run(
            command.resolved_argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        ok = proc.returncode == 0
        return CheckResult(
            status="passed" if ok else "failed",
            command_id=command.command_id,
            exit_code=proc.returncode,
            stdout_ref=proc.stdout[:2000] if proc.stdout else None,
            stderr_ref=proc.stderr[:2000] if proc.stderr else None,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(status="failed", command_id=command.command_id, stderr_ref="timeout")
    except FileNotFoundError:
        return CheckResult(status="failed", command_id=command.command_id, stderr_ref="ruff not found")
    except Exception as exc:
        return CheckResult(status="failed", command_id=command.command_id, stderr_ref=str(exc)[:500])
