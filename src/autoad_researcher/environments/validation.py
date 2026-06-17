"""Generic environment validation registry."""

import json
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.environments.models import EnvironmentPlan, ValidationStep


class ValidationContext(BaseModel):
    """Observed facts supplied to deterministic validators."""

    model_config = ConfigDict(extra="forbid")

    runtime_versions: dict[str, str] = Field(default_factory=dict)
    packages: dict[str, str] = Field(default_factory=dict)
    importable_modules: list[str] = Field(default_factory=list)
    existing_files: list[str] = Field(default_factory=list)
    command_exit_codes: dict[str, int] = Field(default_factory=dict)
    repository_dirty: bool = False
    gpu_available: bool = False
    gpu_compute_ok: bool = False


class ValidationResult(BaseModel):
    """One validation outcome."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    validation_id: str
    kind: str
    status: Literal["passed", "failed", "skipped"]
    code: str
    message: str
    observed: dict[str, Any] = Field(default_factory=dict)
    artifact_paths: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    """Aggregated environment validation report."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    plan_id: str
    run_id: str
    status: Literal["passed", "failed"]
    results: list[ValidationResult]
    required_passed: int
    required_total: int
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StepVerifier(Protocol):
    kind: str

    def verify(self, step: ValidationStep, context: ValidationContext) -> ValidationResult:
        """Verify a step against observed context."""


class RuntimeVersionVerifier:
    kind = "runtime_version"

    def verify(self, step: ValidationStep, context: ValidationContext) -> ValidationResult:
        expected = {k: str(v) for k, v in step.parameters.items()}
        observed = {
            key: context.runtime_versions.get(key)
            for key in expected
        }
        missing = [key for key, value in observed.items() if value is None]
        mismatched = [
            key for key, value in observed.items()
            if value is not None and not str(value).startswith(expected[key])
        ]
        if missing:
            return _failed(step, "ENV_RUNTIME_VERSION_MISMATCH", f"missing runtime version: {missing}", observed)
        if mismatched:
            return _failed(step, "ENV_RUNTIME_VERSION_MISMATCH", f"runtime version mismatch: {mismatched}", observed)
        return _passed(step, "runtime versions matched", observed)


class PackageInventoryVerifier:
    kind = "package_inventory"

    def verify(self, step: ValidationStep, context: ValidationContext) -> ValidationResult:
        expected = step.parameters.get("packages", {})
        observed = {name: context.packages.get(name) for name in expected}
        missing = [name for name, version in observed.items() if version is None]
        mismatched = [
            name for name, version in observed.items()
            if version is not None and str(version) != str(expected[name])
        ]
        if missing:
            return _failed(step, "ENV_PACKAGE_MISSING", f"missing packages: {missing}", observed)
        if mismatched:
            return _failed(step, "ENV_PACKAGE_VERSION_MISMATCH", f"package version mismatch: {mismatched}", observed)
        return _passed(step, "package inventory matched", observed)


class PythonImportVerifier:
    kind = "python_import"

    def verify(self, step: ValidationStep, context: ValidationContext) -> ValidationResult:
        modules = step.parameters.get("modules", [])
        available = set(context.importable_modules)
        missing = [module for module in modules if module not in available]
        observed = {"modules": modules, "missing": missing}
        if missing:
            return _failed(step, "ENV_IMPORT_FAILED", f"import failed: {missing}", observed)
        return _passed(step, "imports available", observed)


class CommandVerifier:
    kind = "command"

    def verify(self, step: ValidationStep, context: ValidationContext) -> ValidationResult:
        exit_code = context.command_exit_codes.get(step.validation_id)
        observed = {"exit_code": exit_code}
        if exit_code is None or exit_code != 0:
            return _failed(step, "ENV_PROJECT_SMOKE_FAILED", "command validation failed", observed)
        return _passed(step, "command validation passed", observed)


class FileExistsVerifier:
    kind = "file_exists"

    def verify(self, step: ValidationStep, context: ValidationContext) -> ValidationResult:
        paths = step.parameters.get("paths", [])
        existing = set(context.existing_files)
        missing = [path for path in paths if path not in existing]
        observed = {"paths": paths, "missing": missing}
        if missing:
            return _failed(step, "ENV_PROJECT_SMOKE_FAILED", f"missing files: {missing}", observed)
        return _passed(step, "files exist", observed)


class RepositoryCleanVerifier:
    kind = "repository_clean"

    def verify(self, step: ValidationStep, context: ValidationContext) -> ValidationResult:
        observed = {"repository_dirty": context.repository_dirty}
        if context.repository_dirty:
            return _failed(step, "ENV_REPOSITORY_MUTATED", "repository is dirty", observed)
        return _passed(step, "repository is clean", observed)


class GpuAvailableVerifier:
    kind = "gpu_available"

    def verify(self, step: ValidationStep, context: ValidationContext) -> ValidationResult:
        observed = {"gpu_available": context.gpu_available}
        if not context.gpu_available:
            return _failed(step, "ENV_GPU_UNAVAILABLE", "GPU unavailable", observed)
        return _passed(step, "GPU available", observed)


class GpuComputeVerifier:
    kind = "gpu_compute"

    def verify(self, step: ValidationStep, context: ValidationContext) -> ValidationResult:
        observed = {"gpu_compute_ok": context.gpu_compute_ok}
        if not context.gpu_compute_ok:
            return _failed(step, "ENV_GPU_COMPUTE_FAILED", "GPU compute failed", observed)
        return _passed(step, "GPU compute passed", observed)


class ProjectSmokeVerifier(CommandVerifier):
    kind = "project_smoke"


VERIFIERS: dict[str, StepVerifier] = {
    "runtime_version": RuntimeVersionVerifier(),
    "package_inventory": PackageInventoryVerifier(),
    "python_import": PythonImportVerifier(),
    "command": CommandVerifier(),
    "file_exists": FileExistsVerifier(),
    "repository_clean": RepositoryCleanVerifier(),
    "gpu_available": GpuAvailableVerifier(),
    "gpu_compute": GpuComputeVerifier(),
    "project_smoke": ProjectSmokeVerifier(),
}


def validate_environment(
    plan: EnvironmentPlan,
    context: ValidationContext,
    output_dir: Path | str | None = None,
) -> ValidationReport:
    """Run validation_steps against observed context."""
    results = []
    for step in plan.validation_steps:
        verifier = VERIFIERS[step.kind]
        results.append(verifier.verify(step, context))

    required_results = [
        result
        for step, result in zip(plan.validation_steps, results, strict=True)
        if step.required
    ]
    required_passed = sum(1 for result in required_results if result.status == "passed")
    required_total = len(required_results)
    status = "passed" if required_passed == required_total else "failed"
    payload = {
        "schema_version": 1,
        "plan_id": plan.plan_id,
        "run_id": plan.run_id,
        "status": status,
        "results": [r.model_dump(mode="json") for r in results],
        "required_passed": required_passed,
        "required_total": required_total,
    }
    payload["report_sha256"] = canonical_sha256(payload)
    report = ValidationReport.model_validate(payload)
    if output_dir is not None:
        _write_report(Path(output_dir) / "validation_report.json", report)
    return report


def _passed(step: ValidationStep, message: str, observed: dict[str, Any]) -> ValidationResult:
    return ValidationResult(
        validation_id=step.validation_id,
        kind=step.kind,
        status="passed",
        code="OK",
        message=message,
        observed=observed,
        artifact_paths=[],
    )


def _failed(
    step: ValidationStep,
    code: str,
    message: str,
    observed: dict[str, Any],
) -> ValidationResult:
    return ValidationResult(
        validation_id=step.validation_id,
        kind=step.kind,
        status="failed",
        code=code,
        message=message,
        observed=observed,
        artifact_paths=[],
    )


def _write_report(path: Path, report: ValidationReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
