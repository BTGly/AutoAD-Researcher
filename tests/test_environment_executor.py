"""Tests for generic environment command execution."""

from pathlib import Path

from autoad_researcher.environments import (
    CommandExecutionOutput,
    ResolvedCommand,
    execute_resolved_command,
)


def command(**overrides) -> ResolvedCommand:
    data = {
        "step_id": "step_one",
        "program": "python",
        "args": ["--version"],
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 30,
    }
    data.update(overrides)
    return ResolvedCommand(**data)


def test_execute_with_injected_success_runner(tmp_path: Path):
    def runner(cmd: ResolvedCommand) -> CommandExecutionOutput:
        assert cmd.argv == ["python", "--version"]
        return CommandExecutionOutput(exit_code=0, stdout="ok\n", stderr="")

    result = execute_resolved_command(command(), tmp_path, runner=runner)

    assert result.status == "success"
    assert result.exit_code == 0
    assert (tmp_path / result.stdout_path).read_text(encoding="utf-8") == "ok\n"
    assert (tmp_path / result.stderr_path).read_text(encoding="utf-8") == ""


def test_execute_with_injected_failure_runner(tmp_path: Path):
    def runner(cmd: ResolvedCommand) -> CommandExecutionOutput:
        return CommandExecutionOutput(exit_code=7, stdout="", stderr="failed")

    result = execute_resolved_command(command(), tmp_path, runner=runner)

    assert result.status == "failed"
    assert result.failure_code == "ENV_COMMAND_FAILED"
    assert result.exit_code == 7
    assert (tmp_path / result.stderr_path).read_text(encoding="utf-8") == "failed"


def test_execute_with_injected_timeout_runner(tmp_path: Path):
    def runner(cmd: ResolvedCommand) -> CommandExecutionOutput:
        return CommandExecutionOutput(exit_code=None, stdout="", stderr="", timed_out=True)

    result = execute_resolved_command(command(), tmp_path, runner=runner)

    assert result.status == "timeout"
    assert result.failure_code == "ENV_COMMAND_TIMEOUT"
    assert result.exit_code is None


def test_secret_environment_values_are_redacted(tmp_path: Path):
    cmd = command(environment={"API_TOKEN": "secret-value"})

    def runner(_: ResolvedCommand) -> CommandExecutionOutput:
        return CommandExecutionOutput(
            exit_code=0,
            stdout="token=secret-value",
            stderr="secret-value failed once",
        )

    result = execute_resolved_command(cmd, tmp_path, runner=runner)

    assert "secret-value" not in (tmp_path / result.stdout_path).read_text(encoding="utf-8")
    assert "secret-value" not in (tmp_path / result.stderr_path).read_text(encoding="utf-8")


def test_default_subprocess_uses_argv_without_shell(tmp_path: Path):
    result = execute_resolved_command(
        command(
            args=["-c", "print('hello')"],
            cwd=str(tmp_path),
        ),
        tmp_path,
    )

    assert result.status == "success"
    assert (tmp_path / result.stdout_path).read_text(encoding="utf-8") == "hello\n"
