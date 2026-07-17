"""Collect validation facts from the interpreter that was actually prepared."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.environments.models import EnvironmentPlan
from autoad_researcher.environments.probe import (
    ProbeCommandResult,
    ProbeRunner,
    RepositoryProbe,
    _run_probe_command,
)
from autoad_researcher.environments.validation import ValidationContext


class CollectedValidationContext(BaseModel):
    """Observed context plus immutable evidence needed for a final snapshot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    python_executable: str
    context: ValidationContext
    package_inventory_sha256: str
    command_results: list[ProbeCommandResult]
    repository_commit: str | None = None
    gpu_capability: list[dict[str, str]] = []


def collect_validation_context(
    plan: EnvironmentPlan,
    *,
    python_executable: str | Path,
    repository_probe: RepositoryProbe | None,
    output_dir: Path,
    runner: ProbeRunner | None = None,
) -> CollectedValidationContext:
    """Execute only deterministic probes through ``shell=False`` command paths."""
    requested_modules = _requested_modules(plan)
    source = _validation_probe_source(requested_modules)
    command, stdout = _run_probe_command(
        "validation_runtime",
        [str(python_executable), "-c", source],
        output_dir,
        runner=runner,
    )
    payload = _parse_payload(stdout) if command.status == "success" else {}
    packages = payload.get("packages") if isinstance(payload.get("packages"), dict) else {}
    importable = payload.get("importable_modules")
    runtime_versions = payload.get("runtime_versions")
    torch = payload.get("torch") if isinstance(payload.get("torch"), dict) else {}
    context = ValidationContext(
        runtime_versions={str(key): str(value) for key, value in (runtime_versions or {}).items()},
        packages={str(key): str(value) for key, value in packages.items()},
        importable_modules=[str(item) for item in importable] if isinstance(importable, list) else [],
        existing_files=_existing_files(repository_probe),
        repository_dirty=repository_probe.repository_dirty if repository_probe else False,
        gpu_available=bool(torch.get("cuda_available")),
        gpu_compute_ok=bool(torch.get("gpu_compute_ok")),
    )
    collected = CollectedValidationContext(
        python_executable=str(python_executable),
        context=context,
        package_inventory_sha256=canonical_sha256({"packages": context.packages}),
        command_results=[command],
        repository_commit=repository_probe.repository_commit if repository_probe else None,
        gpu_capability=list(torch.get("gpu_capability", [])) if isinstance(torch.get("gpu_capability"), list) else [],
    )
    write_validation_context(output_dir / "validation_context.json", collected)
    return collected


def write_validation_context(path: Path, context: CollectedValidationContext) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(context.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _requested_modules(plan: EnvironmentPlan) -> list[str]:
    result: list[str] = []
    for step in plan.validation_steps:
        if step.kind != "python_import":
            continue
        modules = step.parameters.get("modules", [])
        if not isinstance(modules, list):
            continue
        for module in modules:
            if isinstance(module, str) and module not in result:
                result.append(module)
    return result


def _validation_probe_source(modules: list[str]) -> str:
    encoded_modules = json.dumps(modules)
    return f"""
import importlib, importlib.metadata, json, platform, sys
modules = json.loads({encoded_modules!r})
packages = {{}}
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get('Name')
    if name:
        packages[str(name)] = str(distribution.version)
importable = []
for module in modules:
    try:
        importlib.import_module(module)
        importable.append(module)
    except Exception:
        pass
torch = {{"cuda_available": False, "gpu_compute_ok": False, "gpu_capability": []}}
try:
    import torch as _torch
    torch["version"] = _torch.__version__
    torch["cuda_runtime"] = _torch.version.cuda
    torch["cuda_available"] = bool(_torch.cuda.is_available())
    if torch["cuda_available"]:
        try:
            value = _torch.tensor([1.0], device="cuda")
            torch["gpu_compute_ok"] = bool((value + value).item() == 2.0)
            properties = _torch.cuda.get_device_properties(0)
            torch["gpu_capability"] = [{{"name": properties.name, "memory_mb": str(properties.total_memory // (1024 * 1024))}}]
        except Exception as exc:
            torch["gpu_compute_error"] = f"{{type(exc).__name__}}: {{exc}}"
except Exception as exc:
    torch["error"] = f"{{type(exc).__name__}}: {{exc}}"
print(json.dumps({{
  "runtime_versions": {{"python": platform.python_version(), "platform": f"{{sys.platform}}_{{platform.machine()}}"}},
  "packages": packages, "importable_modules": importable, "torch": torch,
}}, sort_keys=True))
"""


def _existing_files(repository_probe: RepositoryProbe | None) -> list[str]:
    if repository_probe is None:
        return []
    return list(dict.fromkeys(
        repository_probe.dependency_files
        + repository_probe.readme_files
        + repository_probe.entrypoint_candidates
    ))


def _parse_payload(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
