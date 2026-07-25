"""Bounded, repository-declared validation before experiment execution."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.experiment.executor_adapters import ExecutorAdapterEvidence
from autoad_researcher.experiment.preparation import PreparationEvidence, PreparationStore, empty_preparation


PreflightStatus = Literal["passed", "failed", "blocked"]


class PreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    status: Literal["passed", "failed", "skipped"]
    command: list[str] = Field(min_length=1)
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_sha256: str
    stderr_sha256: str
    failure_message: str | None = None


class AdapterPreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    adapter_id: str = Field(min_length=1)
    status: PreflightStatus
    required_checks: list[str] = Field(default_factory=list)
    checks: list[PreflightCheck] = Field(default_factory=list)
    preflight_sha256: str
    artifact_ref: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def run_adapter_preflight(
    repository_root: Path,
    evidence: ExecutorAdapterEvidence,
    *,
    required_checks: list[str] | None = None,
    timeout_seconds: int = 60,
    output_limit: int = 16_384,
    artifact_path: Path | None = None,
) -> AdapterPreflightResult:
    """Run only commands explicitly declared by the adapter manifest.

    The repository is never installed or modified here. A temporary HOME and
    stripped proxy variables prevent the check from silently using external
    credentials or network proxies. The command itself is still controlled by
    the adapter owner and therefore its exact argv is retained as evidence.
    """

    requested = list(required_checks or evidence.preflight_commands)
    if evidence.preflight_required and not requested:
        result = _result(
            evidence.adapter_id,
            "blocked",
            requested,
            [],
            "adapter requires preflight but declares no preflight commands",
        )
        return _write_result(result, artifact_path)
    missing = [name for name in requested if name not in evidence.preflight_commands]
    checks: list[PreflightCheck] = []
    if missing:
        result = _result(
            evidence.adapter_id,
            "blocked",
            requested,
            checks,
            f"missing declared preflight checks: {', '.join(missing)}",
        )
        return _write_result(result, artifact_path)

    with TemporaryDirectory(prefix="autoad-preflight-home-") as home:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
        }
        environment.update({"HOME": home, "PYTHONDONTWRITEBYTECODE": "1"})
        for name in requested:
            command = evidence.preflight_commands[name]
            argv = list(command.args)
            validation_error = _validate_argv(argv)
            if validation_error is not None:
                checks.append(_failed_check(name, argv, validation_error))
                continue
            command_environment = dict(environment)
            command_environment.update(command.environment)
            try:
                completed = subprocess.run(
                    argv,
                    cwd=repository_root,
                    env=command_environment,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                    shell=False,
                )
                stdout = completed.stdout[:output_limit]
                stderr = completed.stderr[:output_limit]
                passed = completed.returncode == 0
                checks.append(
                    PreflightCheck(
                        name=name,
                        status="passed" if passed else "failed",
                        command=argv,
                        exit_code=completed.returncode,
                        stdout=stdout,
                        stderr=stderr,
                        stdout_sha256=_sha256(stdout),
                        stderr_sha256=_sha256(stderr),
                        failure_message=None if passed else f"exit code {completed.returncode}",
                    )
                )
                if not passed and command.required:
                    break
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
                checks.append(_failed_check(name, argv, str(exc)))
                if command.required:
                    break

    failed_required = any(item.status == "failed" for item in checks if _is_required(item.name, evidence))
    missing_required = any(name not in {item.name for item in checks} and _is_required(name, evidence) for name in requested)
    status: PreflightStatus = "failed" if failed_required else "blocked" if missing_required else "passed"
    result = AdapterPreflightResult(
        adapter_id=evidence.adapter_id,
        status=status,
        required_checks=requested,
        checks=checks,
        preflight_sha256="0" * 64,
    )
    result = result.model_copy(update={"preflight_sha256": canonical_sha256(result.model_dump(mode="json", exclude={"preflight_sha256", "artifact_ref"}))})
    return _write_result(result, artifact_path)


def _result(adapter_id: str, status: PreflightStatus, required: list[str], checks: list[PreflightCheck], message: str) -> AdapterPreflightResult:
    result = AdapterPreflightResult(
        adapter_id=adapter_id,
        status=status,
        required_checks=required,
        checks=checks,
        preflight_sha256="0" * 64,
    )
    result = result.model_copy(update={"checks": [*checks, _failed_check("preflight_contract", ["<contract>"], message)]})
    return result.model_copy(update={"preflight_sha256": canonical_sha256(result.model_dump(mode="json", exclude={"preflight_sha256", "artifact_ref"}))})


def _write_result(result: AdapterPreflightResult, artifact_path: Path | None) -> AdapterPreflightResult:
    if artifact_path is None:
        return result
    persisted = result.model_copy(update={"artifact_ref": artifact_path.as_posix()})
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with artifact_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(persisted.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return persisted


def persist_preflight_evidence(run_dir: Path, result: AdapterPreflightResult) -> AdapterPreflightResult:
    """Bind preflight observations into the preparation evidence ledger."""

    preparation = PreparationStore().load(run_dir) or empty_preparation(run_dir.name)
    existing = {item.evidence_id: item for item in preparation.evidence}
    for check in result.checks:
        evidence_id = f"preflight_{result.adapter_id}_{check.name}"
        existing[evidence_id] = PreparationEvidence(
            evidence_id=evidence_id,
            kind="verified" if check.status == "passed" else "observed",
            summary=f"preflight {check.name}: {check.status}",
            command=check.command,
            exit_code=check.exit_code,
            output=check.stdout,
            output_sha256=check.stdout_sha256,
            artifact_ref=result.artifact_ref,
        )
    PreparationStore().save(
        run_dir,
        preparation.model_copy(update={"evidence": list(existing.values()), "investigation_status": "complete"}),
    )
    return result


def ensure_adapter_preflight(
    repository_root: Path,
    evidence: ExecutorAdapterEvidence,
    *,
    run_dir: Path,
    artifact_name: str,
    timeout_seconds: int = 60,
) -> AdapterPreflightResult | None:
    """Run mandatory adapter checks while keeping legacy fixtures compatible."""

    if not evidence.preflight_required and not evidence.preflight_commands:
        return None
    result = run_adapter_preflight(
        repository_root,
        evidence,
        timeout_seconds=timeout_seconds,
        artifact_path=run_dir / "experiments" / "preflight" / f"{artifact_name}.json",
    )
    persist_preflight_evidence(run_dir, result)
    if not result.passed:
        detail = result.checks[-1].failure_message if result.checks else "no checks"
        raise ValueError(f"adapter preflight {result.status}: {detail}")
    return result


def _failed_check(name: str, argv: list[str], message: str) -> PreflightCheck:
    return PreflightCheck(
        name=name,
        status="failed",
        command=argv or ["<empty>"],
        stdout_sha256=_sha256(""),
        stderr_sha256=_sha256(""),
        failure_message=message,
    )


def _validate_argv(argv: list[str]) -> str | None:
    if not argv:
        return "preflight argv must not be empty"
    executable = Path(argv[0]).name
    allowed = {Path(sys.executable).name, "python", "python3"}
    if executable not in allowed:
        return f"preflight executable is not allowed: {argv[0]}"
    for arg in argv:
        if "\x00" in arg:
            return "NUL byte forbidden in preflight argv"
        if any(token in arg for token in ["|", ">", "<", "&&", "||", ";", "`", "$("]):
            return "shell metacharacter forbidden in preflight argv"
    return None


def _is_required(name: str, evidence: ExecutorAdapterEvidence) -> bool:
    command = evidence.preflight_commands.get(name)
    return command is None or command.required


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
