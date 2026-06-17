"""Tests for EnvironmentSnapshot contracts."""

from autoad_researcher.environments import (
    EnvironmentSnapshot,
    InstalledPackage,
    environment_snapshot_sha256,
    snapshot_from_plan,
)
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
