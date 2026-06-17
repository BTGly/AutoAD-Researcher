"""Tests for EnvironmentPlan policy gate and fixtures."""

from pathlib import Path

import pytest

from autoad_researcher.environments import (
    EnvironmentPlan,
    EnvironmentPlanPolicyError,
    evaluate_environment_plan_policy,
    load_environment_plan,
    validate_environment_plan_policy,
)
from tests.test_environment_plan_models import valid_plan

FIXTURE_DIR = Path("fixtures/environment_plans")


def plan_with(**overrides) -> EnvironmentPlan:
    return EnvironmentPlan.model_validate(valid_plan(**overrides))


def violation_codes(plan: EnvironmentPlan) -> set[str]:
    return {v.code for v in evaluate_environment_plan_policy(plan).violations}


def test_valid_policy_passes():
    plan = plan_with()

    report = validate_environment_plan_policy(plan)

    assert report.status == "passed"
    assert report.plan_sha256


def test_all_environment_plan_fixtures_pass_policy():
    paths = sorted(FIXTURE_DIR.glob("*.yaml"))
    assert {p.name for p in paths} == {
        "existing_python.yaml",
        "python_cpu_uv.yaml",
        "python_cuda_uv.yaml",
    }

    for path in paths:
        plan = load_environment_plan(path)
        report = validate_environment_plan_policy(plan)
        assert report.status == "passed"


def test_duplicate_step_id_rejected():
    data = valid_plan()
    data["build_steps"].append(dict(data["build_steps"][0]))

    plan = EnvironmentPlan.model_validate(data)

    assert "ENV_PLAN_POLICY_DENIED" in violation_codes(plan)


def test_no_required_validation_rejected():
    data = valid_plan()
    data["validation_steps"][0]["required"] = False

    plan = EnvironmentPlan.model_validate(data)

    with pytest.raises(EnvironmentPlanPolicyError) as exc:
        validate_environment_plan_policy(plan)
    assert exc.value.report.violations[0].code == "ENV_PLAN_POLICY_DENIED"


def test_shell_metacharacter_rejected():
    data = valid_plan()
    data["build_steps"][0]["args"] = ["install", "pkg", "|", "bash"]

    plan = EnvironmentPlan.model_validate(data)

    assert "ENV_COMMAND_FORBIDDEN" in violation_codes(plan)


def test_cwd_traversal_rejected():
    data = valid_plan()
    data["build_steps"][0]["cwd"] = "workspace/repos/project/../../outside"

    plan = EnvironmentPlan.model_validate(data)

    assert "ENV_PATH_OUTSIDE_WORKSPACE" in violation_codes(plan)


def test_environment_path_escape_rejected():
    data = valid_plan()
    data["target"]["environment_path"] = "workspace/cache/env"

    plan = EnvironmentPlan.model_validate(data)

    assert "ENV_PATH_OUTSIDE_WORKSPACE" in violation_codes(plan)


def test_system_install_without_approval_rejected():
    data = valid_plan()
    data["build_steps"][0]["program"] = "apt-get"
    data["build_steps"][0]["args"] = ["install", "python3-dev"]

    plan = EnvironmentPlan.model_validate(data)

    codes = violation_codes(plan)
    assert "ENV_COMMAND_FORBIDDEN" in codes
    assert "ENV_APPROVAL_REQUIRED" in codes


def test_repository_modification_without_approval_rejected():
    data = valid_plan()
    data["build_steps"][0]["modifies_repository"] = True

    plan = EnvironmentPlan.model_validate(data)

    assert "ENV_APPROVAL_REQUIRED" in violation_codes(plan)


def test_build_network_must_match_permissions():
    data = valid_plan()
    data["build_steps"][0]["network"] = True
    data["permissions"]["network_during_build"] = False

    plan = EnvironmentPlan.model_validate(data)

    assert "ENV_PLAN_POLICY_DENIED" in violation_codes(plan)


def test_unapproved_environment_variable_rejected():
    data = valid_plan()
    data["build_steps"][0]["environment"] = {"OPENAI_API_KEY": "secret"}

    plan = EnvironmentPlan.model_validate(data)

    assert "ENV_PLAN_POLICY_DENIED" in violation_codes(plan)


def test_high_risk_assumption_requires_validation():
    data = valid_plan()
    data["assumptions"][0]["risk"] = "high"
    data["assumptions"][0]["validation_id"] = None

    plan = EnvironmentPlan.model_validate(data)

    assert "ENV_APPROVAL_REQUIRED" in violation_codes(plan)
