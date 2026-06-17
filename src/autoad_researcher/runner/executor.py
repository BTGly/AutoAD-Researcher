"""Immutable experiment attempt execution."""

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.environments.result import CommandExecutionOutput, ResolvedCommand
from autoad_researcher.runner.models import (
    ExperimentCommandPlan,
    ExperimentExecutionResult,
    ExperimentInputRefs,
    OutputManifest,
    OutputManifestEntry,
)

ExperimentRunner = Callable[[ResolvedCommand, Path], CommandExecutionOutput]
RepositoryFingerprintProbe = Callable[[], str]


def experiment_command_sha256(plan: ExperimentCommandPlan) -> str:
    return canonical_sha256(plan)


def execute_experiment_attempt(
    *,
    run_id: str,
    attempt: str,
    command_plan: ExperimentCommandPlan,
    input_refs: ExperimentInputRefs,
    attempt_dir: Path | str,
    runner: ExperimentRunner,
    repository_fingerprint_after: str | RepositoryFingerprintProbe | None = None,
) -> ExperimentExecutionResult:
    """Execute one immutable experiment attempt with injected runner."""
    out_dir = Path(attempt_dir)
    if out_dir.exists():
        raise FileExistsError(f"attempt output dir already exists: {out_dir}")
    out_dir.mkdir(parents=True)

    command_sha = experiment_command_sha256(command_plan)
    if command_sha != input_refs.command_sha256:
        return _failed_result(
            run_id=run_id,
            attempt=attempt,
            plan=command_plan,
            command_sha=command_sha,
            out_dir=out_dir,
            status="preflight_failed",
            code="RUN_COMMAND_HASH_MISMATCH",
            message="command SHA does not match input refs",
        )

    raw = runner(_resolved_command(command_plan), out_dir)
    stdout_rel = "stdout.log"
    stderr_rel = "stderr.log"
    (out_dir / stdout_rel).write_text(raw.stdout, encoding="utf-8")
    (out_dir / stderr_rel).write_text(raw.stderr, encoding="utf-8")

    if raw.timed_out:
        return _failed_result(
            run_id=run_id,
            attempt=attempt,
            plan=command_plan,
            command_sha=command_sha,
            out_dir=out_dir,
            status="execution_failed",
            code="RUN_TIMEOUT",
            message="experiment command timed out",
            exit_code=raw.exit_code,
            timed_out=True,
        )
    if raw.exit_code != 0:
        return _failed_result(
            run_id=run_id,
            attempt=attempt,
            plan=command_plan,
            command_sha=command_sha,
            out_dir=out_dir,
            status="execution_failed",
            code="RUN_COMMAND_FAILED",
            message=f"experiment command exited with code {raw.exit_code}",
            exit_code=raw.exit_code,
        )

    actual_repository_fingerprint_after = _resolve_repository_fingerprint_after(
        repository_fingerprint_after
    )
    if actual_repository_fingerprint_after is not None and (
        actual_repository_fingerprint_after != input_refs.repository_fingerprint
    ):
        return _failed_result(
            run_id=run_id,
            attempt=attempt,
            plan=command_plan,
            command_sha=command_sha,
            out_dir=out_dir,
            status="invalid_repository_mutation",
            code="RUN_REPOSITORY_MUTATED",
            message="repository fingerprint changed during execution",
            exit_code=raw.exit_code,
        )

    missing = [
        path for path in command_plan.expected_outputs
        if not (out_dir / path).is_file()
    ]
    if missing:
        return _failed_result(
            run_id=run_id,
            attempt=attempt,
            plan=command_plan,
            command_sha=command_sha,
            out_dir=out_dir,
            status="execution_failed",
            code="RUN_EXPECTED_OUTPUT_MISSING",
            message=f"missing expected outputs: {missing}",
            exit_code=raw.exit_code,
        )

    manifest = _build_output_manifest(out_dir, command_plan.expected_outputs)
    _write_json(out_dir / "output_manifest.json", manifest.model_dump(mode="json"))
    result = ExperimentExecutionResult(
        schema_version=1,
        run_id=run_id,
        attempt=attempt,
        command_id=command_plan.command_id,
        command_sha256=command_sha,
        status="success",
        exit_code=raw.exit_code,
        timed_out=False,
        stdout_path=stdout_rel,
        stderr_path=stderr_rel,
        output_manifest_path="output_manifest.json",
    )
    _write_json(out_dir / "execution_result.json", result.model_dump(mode="json", exclude_none=True))
    return result


def _resolve_repository_fingerprint_after(
    value: str | RepositoryFingerprintProbe | None,
) -> str | None:
    if value is None:
        return None
    if callable(value):
        return value()
    return value


def run_experiment_subprocess(
    command: ResolvedCommand,
    attempt_dir: Path,
) -> CommandExecutionOutput:
    """Run an experiment command with shell=False and captured logs."""
    del attempt_dir
    env = os.environ.copy()
    env.update(command.environment)
    try:
        completed = subprocess.run(
            command.argv,
            cwd=command.cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=command.timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandExecutionOutput(
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
        )
    return CommandExecutionOutput(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        timed_out=False,
    )


def _resolved_command(plan: ExperimentCommandPlan) -> ResolvedCommand:
    return ResolvedCommand(
        step_id=plan.command_id,
        program=plan.program,
        args=plan.args,
        cwd=plan.cwd,
        environment=plan.environment,
        timeout_seconds=plan.timeout_seconds,
    )


def _build_output_manifest(attempt_dir: Path, outputs: list[str]) -> OutputManifest:
    entries = [
        OutputManifestEntry(
            path=path,
            sha256=sha256_file(attempt_dir / path),
            size_bytes=(attempt_dir / path).stat().st_size,
        )
        for path in outputs
    ]
    payload = {
        "schema_version": 1,
        "outputs": [entry.model_dump(mode="json") for entry in entries],
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return OutputManifest.model_validate(payload)


def _failed_result(
    *,
    run_id: str,
    attempt: str,
    plan: ExperimentCommandPlan,
    command_sha: str,
    out_dir: Path,
    status: str,
    code: str,
    message: str,
    exit_code: int | None = None,
    timed_out: bool = False,
) -> ExperimentExecutionResult:
    stdout_rel = "stdout.log"
    stderr_rel = "stderr.log"
    (out_dir / stdout_rel).touch(exist_ok=True)
    (out_dir / stderr_rel).touch(exist_ok=True)
    result = ExperimentExecutionResult(
        schema_version=1,
        run_id=run_id,
        attempt=attempt,
        command_id=plan.command_id,
        command_sha256=command_sha,
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
        stdout_path=stdout_rel,
        stderr_path=stderr_rel,
        failure_code=code,
        failure_message=message,
    )
    _write_json(out_dir / "execution_result.json", result.model_dump(mode="json", exclude_none=True))
    return result


def _write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
