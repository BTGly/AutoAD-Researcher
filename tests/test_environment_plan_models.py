"""Tests for generic EnvironmentPlan contracts."""

from pathlib import Path

import pytest

from autoad_researcher.environments import (
    EnvironmentPlan,
    EnvironmentPlanRevision,
    environment_plan_sha256,
    load_environment_plan,
    write_environment_plan,
)


def valid_plan(**overrides):
    data = {
        "schema_version": 1,
        "plan_id": "plan_cpu_uv_v0",
        "run_id": "run_env_fixture",
        "revision": 0,
        "target": {
            "kind": "python_uv_venv",
            "environment_path": "workspace/envs/cpu_uv",
            "runtime_requirements": {"python": "3.11", "platform": "linux_x86_64"},
            "repository_path": "workspace/repos/cpu_project",
        },
        "evidence": [
            {
                "source_type": "repository",
                "path_or_id": "pyproject.toml",
                "claim": "Project declares Python package metadata.",
                "sha256": "a" * 64,
            }
        ],
        "assumptions": [
            {
                "assumption_id": "python_311_available",
                "statement": "Python 3.11 is available through uv.",
                "risk": "low",
                "validation_id": "check_python",
            }
        ],
        "build_steps": [
            {
                "step_id": "create_env",
                "program": "uv",
                "args": ["venv", "workspace/envs/cpu_uv", "--python", "3.11"],
                "cwd": "workspace/repos/cpu_project",
                "environment": {"UV_LINK_MODE": "copy"},
                "timeout_seconds": 120,
                "network": False,
                "modifies_repository": False,
                "requires_approval": False,
            }
        ],
        "validation_steps": [
            {
                "validation_id": "check_python",
                "kind": "runtime_version",
                "parameters": {"python": "3.11"},
                "required": True,
                "timeout_seconds": 30,
                "network": False,
            }
        ],
        "permissions": {
            "network_during_build": False,
            "network_during_validation": False,
            "allow_system_package_install": False,
            "allow_repository_modification": False,
            "allow_global_environment_mutation": False,
            "max_revision_count": 2,
        },
        "created_by": "fixture",
    }
    data.update(overrides)
    return data


def test_valid_environment_plan_contract():
    plan = EnvironmentPlan.model_validate(valid_plan())

    assert plan.target.kind == "python_uv_venv"
    assert plan.permissions.max_revision_count == 2


def test_extra_fields_rejected():
    data = valid_plan(extra_field=True)

    with pytest.raises(Exception):
        EnvironmentPlan.model_validate(data)


def test_empty_evidence_rejected():
    data = valid_plan(evidence=[])

    with pytest.raises(Exception):
        EnvironmentPlan.model_validate(data)


def test_validation_network_rejected():
    data = valid_plan()
    data["validation_steps"][0]["network"] = True

    with pytest.raises(Exception):
        EnvironmentPlan.model_validate(data)


def test_nul_arg_rejected():
    data = valid_plan()
    data["build_steps"][0]["args"] = ["ok", "bad\x00arg"]

    with pytest.raises(ValueError, match="NUL"):
        EnvironmentPlan.model_validate(data)


def test_revision_parent_rules():
    data = valid_plan(revision=1)

    with pytest.raises(ValueError, match="parent_plan_id"):
        EnvironmentPlan.model_validate(data)

    revised = EnvironmentPlan.model_validate(
        valid_plan(
            plan_id="plan_cpu_uv_v1",
            revision=1,
            parent_plan_id="plan_cpu_uv_v0",
        )
    )
    assert revised.parent_plan_id == "plan_cpu_uv_v0"


def test_environment_plan_revision_parentage():
    revised = EnvironmentPlan.model_validate(
        valid_plan(
            plan_id="plan_cpu_uv_v1",
            revision=1,
            parent_plan_id="plan_cpu_uv_v0",
        )
    )

    revision = EnvironmentPlanRevision(
        schema_version=1,
        parent_plan_id="plan_cpu_uv_v0",
        revision=1,
        reason="install failed",
        evidence=[
            {
                "source_type": "previous_error",
                "path_or_id": "build_v0/step_results.json",
                "claim": "The first build failed during dependency installation.",
            }
        ],
        replacement_plan=revised,
    )

    assert revision.replacement_plan.plan_id == "plan_cpu_uv_v1"


def test_environment_plan_sha_is_stable():
    plan = EnvironmentPlan.model_validate(valid_plan())

    assert environment_plan_sha256(plan) == environment_plan_sha256(plan)


def test_plan_json_roundtrip(tmp_path: Path):
    plan = EnvironmentPlan.model_validate(valid_plan())
    path = tmp_path / "plan.json"

    write_environment_plan(plan, path)
    loaded = load_environment_plan(path)

    assert loaded == plan


def test_plan_yaml_load(tmp_path: Path):
    path = tmp_path / "plan.yaml"
    path.write_text(
        """
schema_version: 1
plan_id: plan_existing_v0
run_id: run_env_fixture
revision: 0
target:
  kind: existing_python
  environment_path: null
  runtime_requirements:
    python: "3.11"
  repository_path: workspace/repos/cpu_project
evidence:
  - source_type: host
    path_or_id: host_capabilities.json
    claim: Current Python is available for validation.
assumptions: []
build_steps:
  - step_id: inspect_python
    program: python
    args: ["--version"]
    cwd: workspace/repos/cpu_project
    environment: {}
    timeout_seconds: 30
    network: false
    modifies_repository: false
    requires_approval: false
validation_steps:
  - validation_id: check_python
    kind: runtime_version
    parameters:
      python: "3.11"
    required: true
    timeout_seconds: 30
    network: false
permissions:
  network_during_build: false
  network_during_validation: false
  allow_system_package_install: false
  allow_repository_modification: false
  allow_global_environment_mutation: false
  max_revision_count: 2
created_by: fixture
""",
        encoding="utf-8",
    )

    plan = load_environment_plan(path)

    assert plan.target.kind == "existing_python"
