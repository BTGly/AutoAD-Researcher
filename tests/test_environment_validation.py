"""Tests for generic environment validation registry."""

import json
from pathlib import Path

from autoad_researcher.environments import (
    VERIFIERS,
    EnvironmentPlan,
    ValidationContext,
    validate_environment,
)
from tests.test_environment_plan_models import valid_plan


def make_plan(**overrides) -> EnvironmentPlan:
    data = valid_plan(**overrides)
    return EnvironmentPlan.model_validate(data)


def test_verifier_registry_contains_documented_kinds():
    assert set(VERIFIERS) == {
        "runtime_version",
        "package_inventory",
        "python_import",
        "command",
        "file_exists",
        "repository_clean",
        "gpu_available",
        "gpu_compute",
        "project_smoke",
    }


def test_runtime_and_import_validation_passes(tmp_path: Path):
    plan = make_plan()
    context = ValidationContext(
        runtime_versions={"python": "3.11.9"},
        importable_modules=["cpu_fixture_project"],
    )

    report = validate_environment(plan, context, tmp_path)

    assert report.status == "passed"
    assert report.required_passed == 1
    assert report.report_sha256
    assert (tmp_path / "validation_report.json").is_file()
    data = json.loads((tmp_path / "validation_report.json").read_text(encoding="utf-8"))
    assert data["status"] == "passed"


def test_required_validation_failure_fails_report():
    plan = make_plan()
    context = ValidationContext(runtime_versions={"python": "3.10"})

    report = validate_environment(plan, context)

    assert report.status == "failed"
    assert report.results[0].code == "ENV_RUNTIME_VERSION_MISMATCH"


def test_package_inventory_validation():
    data = valid_plan()
    data["validation_steps"] = [
        {
            "validation_id": "packages",
            "kind": "package_inventory",
            "parameters": {"packages": {"pydantic": "2.0"}},
            "required": True,
            "timeout_seconds": 30,
            "network": False,
        }
    ]
    plan = EnvironmentPlan.model_validate(data)

    passed = validate_environment(plan, ValidationContext(packages={"pydantic": "2.0"}))
    failed = validate_environment(plan, ValidationContext(packages={"pydantic": "2.1"}))

    assert passed.status == "passed"
    assert failed.results[0].code == "ENV_PACKAGE_VERSION_MISMATCH"


def test_optional_failure_does_not_fail_report():
    data = valid_plan()
    data["validation_steps"] = [
        {
            "validation_id": "required_runtime",
            "kind": "runtime_version",
            "parameters": {"python": "3.11"},
            "required": True,
            "timeout_seconds": 30,
            "network": False,
        },
        {
            "validation_id": "optional_gpu",
            "kind": "gpu_available",
            "parameters": {},
            "required": False,
            "timeout_seconds": 30,
            "network": False,
        },
    ]
    plan = EnvironmentPlan.model_validate(data)
    context = ValidationContext(runtime_versions={"python": "3.11.1"}, gpu_available=False)

    report = validate_environment(plan, context)

    assert report.status == "passed"
    assert report.results[1].status == "failed"
    assert report.results[1].code == "ENV_GPU_UNAVAILABLE"


def test_repository_clean_and_gpu_compute_validation():
    data = valid_plan()
    data["validation_steps"] = [
        {
            "validation_id": "repo_clean",
            "kind": "repository_clean",
            "parameters": {},
            "required": True,
            "timeout_seconds": 30,
            "network": False,
        },
        {
            "validation_id": "gpu_compute",
            "kind": "gpu_compute",
            "parameters": {},
            "required": True,
            "timeout_seconds": 30,
            "network": False,
        },
    ]
    plan = EnvironmentPlan.model_validate(data)

    report = validate_environment(
        plan,
        ValidationContext(repository_dirty=False, gpu_compute_ok=True),
    )

    assert report.status == "passed"


def test_command_and_file_exists_validation():
    data = valid_plan()
    data["validation_steps"] = [
        {
            "validation_id": "cli_help",
            "kind": "command",
            "parameters": {},
            "required": True,
            "timeout_seconds": 30,
            "network": False,
        },
        {
            "validation_id": "expected_file",
            "kind": "file_exists",
            "parameters": {"paths": ["README.md"]},
            "required": True,
            "timeout_seconds": 30,
            "network": False,
        },
    ]
    plan = EnvironmentPlan.model_validate(data)

    report = validate_environment(
        plan,
        ValidationContext(command_exit_codes={"cli_help": 0}, existing_files=["README.md"]),
    )

    assert report.status == "passed"
