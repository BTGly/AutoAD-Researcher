"""Tests for generic environment build step orchestration."""

import json
from pathlib import Path

from autoad_researcher.environments import (
    CommandExecutionOutput,
    ResolvedCommand,
    run_environment_build_steps,
)
from autoad_researcher.environments.models import EnvironmentPlan
from tests.test_environment_plan_models import valid_plan


def make_plan() -> EnvironmentPlan:
    data = valid_plan()
    data["build_steps"].append(
        {
            "step_id": "install_project",
            "program": "uv",
            "args": ["pip", "install", "-e", "."],
            "cwd": "workspace/repos/cpu_project",
            "environment": {"UV_LINK_MODE": "copy"},
            "timeout_seconds": 120,
            "network": False,
            "modifies_repository": False,
            "requires_approval": False,
        }
    )
    return EnvironmentPlan.model_validate(data)


def test_build_steps_success_writes_evidence(tmp_path: Path):
    seen = []

    def runner(cmd: ResolvedCommand) -> CommandExecutionOutput:
        seen.append(cmd.step_id)
        return CommandExecutionOutput(exit_code=0, stdout=f"{cmd.step_id}\n", stderr="")

    result = run_environment_build_steps(make_plan(), tmp_path, runner=runner)

    assert result.status == "success"
    assert seen == ["create_env", "install_project"]
    assert (tmp_path / "step_results.json").is_file()
    assert (tmp_path / "build_result.json").is_file()
    data = json.loads((tmp_path / "build_result.json").read_text(encoding="utf-8"))
    assert data["status"] == "success"
    assert data["adapter"] == "python_uv_venv"
    assert data["snapshot_path"] == "snapshot.json"
    snapshot = json.loads((tmp_path / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["environment_kind"] == "python_uv_venv"
    assert snapshot["environment_sha256"]


def test_build_steps_stop_after_first_failure(tmp_path: Path):
    seen = []

    def runner(cmd: ResolvedCommand) -> CommandExecutionOutput:
        seen.append(cmd.step_id)
        return CommandExecutionOutput(exit_code=9, stdout="", stderr="boom")

    result = run_environment_build_steps(make_plan(), tmp_path, runner=runner)

    assert result.status == "failed"
    assert result.failure_code == "ENV_COMMAND_FAILED"
    assert result.snapshot_path is None
    assert seen == ["create_env"]
    step_results = json.loads((tmp_path / "step_results.json").read_text(encoding="utf-8"))
    assert len(step_results) == 1


def test_build_policy_failure_is_raised_before_execution(tmp_path: Path):
    data = valid_plan()
    data["target"]["environment_path"] = "/tmp/escape"
    plan = EnvironmentPlan.model_validate(data)

    def runner(cmd: ResolvedCommand) -> CommandExecutionOutput:
        raise AssertionError("runner must not be called")

    try:
        run_environment_build_steps(plan, tmp_path, runner=runner)
    except Exception as exc:
        assert "absolute path forbidden" in str(exc)
    else:
        raise AssertionError("policy failure was not raised")
