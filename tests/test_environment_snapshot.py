"""Tests for EnvironmentSnapshot contracts."""

from datetime import datetime, timezone

from autoad_researcher.environments import (
    CollectedValidationContext,
    CommandStepResult,
    EnvironmentBuildResult,
    EnvironmentSnapshot,
    InstalledPackage,
    ValidationContext,
    build_observed_environment_snapshot,
    environment_snapshot_sha256,
    snapshot_from_plan,
)
from autoad_researcher.environments.validation import validate_environment
from autoad_researcher.environments.models import EnvironmentPlan
from tests.test_environment_plan_models import valid_plan


def test_snapshot_from_plan_has_stable_sha():
    plan = EnvironmentPlan.model_validate(valid_plan())

    snapshot = snapshot_from_plan(plan)

    assert snapshot.environment_kind == "python_uv_venv"
    assert snapshot.package_manager == "uv"
    assert snapshot.platform == "linux_x86_64"
    assert snapshot.environment_sha256 == environment_snapshot_sha256(snapshot)


def test_snapshot_sha_ignores_environment_sha_field():
    snapshot = EnvironmentSnapshot(
        schema_version=1,
        environment_kind="existing_python",
        runtime_versions={"python": "3.11"},
        package_manager=None,
        package_manager_version=None,
        packages=[],
        platform="linux_x86_64",
        accelerator=None,
        repository_fingerprint=None,
        environment_sha256="a" * 64,
    )

    modified = snapshot.model_copy(update={"environment_sha256": "b" * 64})

    assert environment_snapshot_sha256(snapshot) == environment_snapshot_sha256(modified)


def test_snapshot_sha_changes_with_package_inventory():
    plan = EnvironmentPlan.model_validate(valid_plan())
    left = snapshot_from_plan(
        plan,
        packages=[InstalledPackage(name="pydantic", version="2.0")],
    )
    right = snapshot_from_plan(
        plan,
        packages=[InstalledPackage(name="pydantic", version="2.1")],
    )

    assert left.environment_sha256 != right.environment_sha256


def test_observed_snapshot_ignores_forged_plan_runtime_values():
    data = valid_plan()
    data["target"]["runtime_requirements"] = {
        "python": "0.0", "platform": "forged", "accelerator": "forged"
    }
    data["validation_steps"][0]["parameters"] = {"python": "3.12"}
    plan = EnvironmentPlan.model_validate(data)
    now = datetime.now(timezone.utc)
    build = EnvironmentBuildResult(
        schema_version=1,
        run_id=plan.run_id,
        plan_id=plan.plan_id,
        plan_sha256="a" * 64,
        status="success",
        adapter="existing_python",
        environment_path=None,
        step_results=[CommandStepResult(
            schema_version=1,
            step_id="verify",
            command_sha256="b" * 64,
            status="success",
            exit_code=0,
            stdout_path="stdout.log",
            stderr_path="stderr.log",
            started_at=now,
            finished_at=now,
            duration_seconds=0,
        )],
        started_at=now,
        finished_at=now,
    )
    report = validate_environment(
        plan,
        ValidationContext(runtime_versions={"python": "3.12.3"}),
    )
    collected = CollectedValidationContext(
        python_executable="/opt/venv/bin/python",
        context=ValidationContext(
            runtime_versions={"python": "3.12.3", "platform": "linux_x86_64", "torch": "2.6", "cuda": "12.4"},
            packages={"torch": "2.6", "pydantic": "2.11"},
            gpu_available=True,
            gpu_compute_ok=True,
        ),
        package_inventory_sha256="c" * 64,
        command_results=[],
        repository_commit="observed-commit",
        repository_fingerprint="d" * 64,
        gpu_capability=[{"name": "Observed GPU", "memory_mb": "40960"}],
    )

    snapshot = build_observed_environment_snapshot(plan, build, collected, report)

    assert snapshot.runtime_versions["python"] == "3.12.3"
    assert snapshot.runtime_versions["platform"] == "linux_x86_64"
    assert snapshot.runtime_versions["torch"] == "2.6"
    assert snapshot.repository_commit == "observed-commit"
    assert snapshot.environment_path == "/opt/venv"
    assert snapshot.validation_report_sha256 == report.report_sha256
    assert snapshot.accelerator is not None
    assert snapshot.accelerator.devices == ["Observed GPU"]
