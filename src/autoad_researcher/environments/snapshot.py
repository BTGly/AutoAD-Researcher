"""Environment snapshot contracts and stable hashing."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.environments.models import EnvironmentPlan


class InstalledPackage(BaseModel):
    """One installed package observed in an environment."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source: str | None = None


class AcceleratorSnapshot(BaseModel):
    """Observed accelerator details."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: str = Field(min_length=1)
    devices: list[str] = Field(default_factory=list)
    runtime_version: str | None = None


class EnvironmentSnapshot(BaseModel):
    """Stable snapshot of an environment build result."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    environment_kind: str = Field(min_length=1)
    runtime_versions: dict[str, str] = Field(default_factory=dict)
    package_manager: str | None = None
    package_manager_version: str | None = None
    packages: list[InstalledPackage] = Field(default_factory=list)
    platform: str = Field(min_length=1)
    accelerator: AcceleratorSnapshot | None = None
    repository_fingerprint: str | None = None
    environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def environment_snapshot_sha256(snapshot: EnvironmentSnapshot | dict[str, Any]) -> str:
    """Hash stable snapshot fields, excluding environment_sha256 itself."""
    if isinstance(snapshot, EnvironmentSnapshot):
        payload = snapshot.model_dump(mode="json", exclude={"environment_sha256"})
    else:
        payload = dict(snapshot)
        payload.pop("environment_sha256", None)
    return canonical_sha256(payload)


def snapshot_from_plan(
    plan: EnvironmentPlan,
    *,
    package_manager_version: str | None = None,
    packages: list[InstalledPackage] | None = None,
    repository_fingerprint: str | None = None,
) -> EnvironmentSnapshot:
    """Create a deterministic build snapshot from plan-declared target facts.

    Later verifier work can replace or enrich package inventory and accelerator
    observations with real probes while preserving the same snapshot contract.
    """
    runtime_versions = dict(plan.target.runtime_requirements)
    platform = runtime_versions.get("platform", "unknown")
    accelerator = _accelerator_from_runtime(runtime_versions)
    payload = {
        "schema_version": 1,
        "environment_kind": plan.target.kind,
        "runtime_versions": runtime_versions,
        "package_manager": _package_manager_for_kind(plan.target.kind),
        "package_manager_version": package_manager_version,
        "packages": [p.model_dump(mode="json", exclude_none=True) for p in packages or []],
        "platform": platform,
        "accelerator": accelerator.model_dump(mode="json", exclude_none=True) if accelerator else None,
        "repository_fingerprint": repository_fingerprint,
    }
    payload["environment_sha256"] = environment_snapshot_sha256(payload)
    return EnvironmentSnapshot.model_validate(payload)


def _package_manager_for_kind(kind: str) -> str | None:
    if kind == "python_uv_venv":
        return "uv"
    if kind == "python_pip_venv":
        return "pip"
    if kind == "conda":
        return "conda"
    return None


def _accelerator_from_runtime(runtime_versions: dict[str, str]) -> AcceleratorSnapshot | None:
    accelerator = runtime_versions.get("accelerator")
    if not accelerator or accelerator == "none":
        return None
    return AcceleratorSnapshot(kind=accelerator)
