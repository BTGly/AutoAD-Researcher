"""Worker execution adapter for the first durable ExperimentAttempt Job."""

from __future__ import annotations

import os
import json
from pathlib import Path, PurePosixPath
from typing import Any

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.environments.result import ResolvedCommand
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.gpu import GpuUnavailableError
from autoad_researcher.runner import ExperimentExecutionResult, execute_experiment_attempt, run_experiment_subprocess


def execute_attempt_job(run_dir: Path, job: dict[str, Any]) -> list[str]:
    """Execute a claimed PipelineJob and persist the terminal Attempt state."""
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    attempt_id = _required_string(payload, "attempt_id")
    job_id = _required_string(job, "job_id")
    store = ExperimentAttemptStore()
    attempt = store.load(run_dir, attempt_id)
    if attempt is None:
        raise FileNotFoundError("experiment Attempt not found")
    attempt = store.mark_starting(run_dir, attempt_id=attempt_id, pipeline_job_id=job_id)
    output_dir = run_dir / "attempts" / attempt.attempt_id
    result_path = output_dir / "execution_result.json"
    lease = None
    worker_id = _worker_id()
    try:
        if attempt.required_device_count:
            from autoad_researcher.experiment.gpu import GpuAllocator

            lease = GpuAllocator().allocate(
                run_dir,
                attempt_id=attempt.attempt_id,
                worker_id=worker_id,
                required_device_count=attempt.required_device_count,
                required_vram_mb=attempt.required_vram_mb,
            )
            attempt = store.bind_resource_lease(
                run_dir, attempt_id=attempt.attempt_id, lease_id=lease.lease_id
            )
        if result_path.is_file():
            result = ExperimentExecutionResult.model_validate_json(result_path.read_text(encoding="utf-8"))
        else:
            result = execute_experiment_attempt(
                run_id=attempt.run_id,
                attempt=attempt.attempt_id,
                command_plan=attempt.command_plan,
                input_refs=attempt.input_refs,
                attempt_dir=output_dir,
                runner=_run_in_run_workspace(run_dir, cuda_visible_devices=lease.cuda_visible_devices if lease else None),
            )
    except GpuUnavailableError as exc:
        result = _write_resource_unavailable_result(attempt, output_dir, str(exc))
        final = store.finish(
            run_dir,
            attempt_id=attempt.attempt_id,
            runtime_status="FAILED",
            failure_code="TEMPORARY_GPU_UNAVAILABLE",
            execution_result_ref=f"attempts/{attempt.attempt_id}/execution_result.json",
        )
        append_event(
            run_dir,
            "experiment.attempt.finalized",
            {
                "attempt_id": final.attempt_id,
                "runtime_status": final.runtime_status,
                "failure_code": final.failure_code,
                "retry_exhausted": final.retry_exhausted,
            },
        )
        raise
    finally:
        if lease is not None:
            from autoad_researcher.experiment.gpu import GpuAllocator

            GpuAllocator().release(run_dir, lease_id=lease.lease_id, worker_id=worker_id)
    runtime_status = "COMPLETED" if result.status == "success" else "TIMED_OUT" if result.timed_out else "FAILED"
    final = store.finish(
        run_dir,
        attempt_id=attempt.attempt_id,
        runtime_status=runtime_status,
        failure_code=result.failure_code,
        execution_result_ref=f"attempts/{attempt.attempt_id}/execution_result.json",
    )
    append_event(
        run_dir,
        "experiment.attempt.finalized",
        {
            "attempt_id": final.attempt_id,
            "runtime_status": final.runtime_status,
            "failure_code": final.failure_code,
            "retry_exhausted": final.retry_exhausted,
        },
    )
    return _outputs(run_dir, output_dir)


def _run_in_run_workspace(run_dir: Path, *, cuda_visible_devices: str | None = None):
    def runner(command: ResolvedCommand, attempt_dir: Path):
        environment = dict(command.environment)
        if cuda_visible_devices is not None:
            environment["CUDA_VISIBLE_DEVICES"] = cuda_visible_devices
        resolved = command.model_copy(
            update={"cwd": str(_resolve_run_relative_path(run_dir, command.cwd)), "environment": environment}
        )
        return run_experiment_subprocess(resolved, attempt_dir)

    return runner


def _resolve_run_relative_path(run_dir: Path, relative_path: str) -> Path:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError("Attempt command cwd must stay within the run directory")
    resolved = run_dir.joinpath(*path.parts).resolve()
    if not resolved.is_relative_to(run_dir.resolve()):
        raise ValueError("Attempt command cwd escapes the run directory")
    return resolved


def _outputs(run_dir: Path, output_dir: Path) -> list[str]:
    return [str(path.relative_to(run_dir)) for path in sorted(output_dir.iterdir()) if path.is_file()]


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"experiment Attempt Job requires {key}")
    return value


def _worker_id() -> str:
    return f"worker-{os.uname().nodename}-{os.getpid()}"


def _write_resource_unavailable_result(attempt, output_dir: Path, message: str) -> ExperimentExecutionResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "stdout.log").write_text("", encoding="utf-8")
    (output_dir / "stderr.log").write_text(message + "\n", encoding="utf-8")
    result = ExperimentExecutionResult(
        schema_version=1,
        run_id=attempt.run_id,
        attempt=attempt.attempt_id,
        command_id=attempt.command_plan.command_id,
        command_sha256=attempt.input_refs.command_sha256,
        status="execution_failed",
        timed_out=False,
        stdout_path="stdout.log",
        stderr_path="stderr.log",
        failure_code="TEMPORARY_GPU_UNAVAILABLE",
        failure_message=message,
    )
    (output_dir / "execution_result.json").write_text(
        json.dumps(result.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result
