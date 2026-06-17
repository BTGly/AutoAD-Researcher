"""Tests for environment adapters."""

import pytest

from autoad_researcher.environments import (
    EnvironmentAdapterError,
    EnvironmentPlan,
    ExistingPythonAdapter,
    PipVenvAdapter,
    UvVenvAdapter,
    get_environment_adapter,
)
from tests.test_environment_plan_models import valid_plan


def make_plan(**overrides) -> EnvironmentPlan:
    data = valid_plan(**overrides)
    return EnvironmentPlan.model_validate(data)


def test_get_environment_adapter_selects_supported_kinds():
    assert isinstance(get_environment_adapter("python_uv_venv"), UvVenvAdapter)
    assert isinstance(get_environment_adapter("python_pip_venv"), PipVenvAdapter)
    assert isinstance(get_environment_adapter("existing_python"), ExistingPythonAdapter)


def test_get_environment_adapter_rejects_unsupported_kind():
    with pytest.raises(EnvironmentAdapterError, match="unsupported"):
        get_environment_adapter("conda")


def test_uv_adapter_translates_build_steps():
    plan = make_plan()

    commands = UvVenvAdapter().prepare_steps(plan)

    assert commands[0].argv[:2] == ["uv", "venv"]


def test_uv_adapter_requires_uv_step():
    data = valid_plan()
    data["build_steps"][0]["program"] = "python"

    with pytest.raises(EnvironmentAdapterError, match="uv build step"):
        UvVenvAdapter().prepare_steps(EnvironmentPlan.model_validate(data))


def test_pip_adapter_translates_python_and_pip_steps():
    data = valid_plan()
    data["target"]["kind"] = "python_pip_venv"
    data["build_steps"] = [
        {
            "step_id": "create_venv",
            "program": "python",
            "args": ["-m", "venv", "workspace/envs/python_pip"],
            "cwd": "workspace/repos/cpu_project",
            "environment": {},
            "timeout_seconds": 120,
            "network": False,
            "modifies_repository": False,
            "requires_approval": False,
        },
        {
            "step_id": "install_requirements",
            "program": "python",
            "args": ["-m", "pip", "install", "-r", "requirements.txt"],
            "cwd": "workspace/repos/cpu_project",
            "environment": {},
            "timeout_seconds": 180,
            "network": True,
            "modifies_repository": False,
            "requires_approval": False,
        },
    ]
    data["permissions"]["network_during_build"] = True
    plan = EnvironmentPlan.model_validate(data)

    commands = PipVenvAdapter().prepare_steps(plan)

    assert commands[0].argv == ["python", "-m", "venv", "workspace/envs/python_pip"]
    assert commands[1].argv[:3] == ["python", "-m", "pip"]


def test_existing_python_adapter_allows_read_only_inspection():
    data = valid_plan()
    data["target"]["kind"] = "existing_python"
    data["target"]["environment_path"] = None
    data["build_steps"] = [
        {
            "step_id": "inspect_python",
            "program": "python",
            "args": ["--version"],
            "cwd": "workspace/repos/cpu_project",
            "environment": {},
            "timeout_seconds": 30,
            "network": False,
            "modifies_repository": False,
            "requires_approval": False,
        }
    ]
    plan = EnvironmentPlan.model_validate(data)

    commands = ExistingPythonAdapter().prepare_steps(plan)

    assert commands[0].argv == ["python", "--version"]


def test_existing_python_adapter_rejects_install():
    data = valid_plan()
    data["target"]["kind"] = "existing_python"
    data["target"]["environment_path"] = None
    data["build_steps"][0]["program"] = "python"
    data["build_steps"][0]["args"] = ["-m", "pip", "install", "."]

    with pytest.raises(EnvironmentAdapterError, match="must not install"):
        ExistingPythonAdapter().prepare_steps(EnvironmentPlan.model_validate(data))
