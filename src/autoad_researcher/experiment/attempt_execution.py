"""Non-blocking Popen lifecycle for durable ExperimentAttempt Jobs."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.gpu import GpuAllocator, GpuUnavailableError
from autoad_researcher.experiment.watchdog import RuntimeWatchdog
from autoad_researcher.experiment.failure_classifier import classify_or_load
from autoad_researcher.experiment.finalizer import finalize_attempt
from autoad_researcher.experiment.retry_policy import RetryPolicy
from autoad_researcher.experiment.health_diagnosis import HealthDiagnosisAgent
from autoad_researcher.runner.models import ExperimentExecutionResult, OutputManifestEntry

_PROCESSES: dict[tuple[str, str], subprocess.Popen[str]] = {}


@dataclass(frozen=True)
class AttemptObservation:
    terminal: bool
    succeeded: bool = False
    outputs: list[str] | None = None
    error: str | None = None


def start_attempt_job(run_dir: Path, job: dict[str, Any]) -> AttemptObservation:
    attempt, job_id = _load_attempt(run_dir, job)
    store = ExperimentAttemptStore()
    attempt = store.mark_starting(run_dir, attempt_id=attempt.attempt_id, pipeline_job_id=job_id)
    output_dir = run_dir / "attempts" / attempt.attempt_id
    if output_dir.exists():
        return _finalize_failure(run_dir, attempt, "RUN_ATTEMPT_ARTIFACT_EXISTS", "attempt artifact directory already exists")
    output_dir.mkdir(parents=True)
    lease = None
    try:
        if attempt.required_device_count:
            lease = GpuAllocator().allocate(run_dir, attempt_id=attempt.attempt_id, worker_id=_worker_id(), required_device_count=attempt.required_device_count, required_vram_mb=attempt.required_vram_mb)
            attempt = store.bind_resource_lease(run_dir, attempt_id=attempt.attempt_id, lease_id=lease.lease_id)
        env = os.environ.copy()
        env.update(attempt.command_plan.environment)
        if lease is not None:
            env["CUDA_VISIBLE_DEVICES"] = lease.cuda_visible_devices
        cwd = _resolve_run_relative_path(run_dir, attempt.command_plan.cwd)
        stdout = (output_dir / "stdout.log").open("w", encoding="utf-8")
        stderr = (output_dir / "stderr.log").open("w", encoding="utf-8")
        process = subprocess.Popen(
            [attempt.command_plan.program, *attempt.command_plan.args],
            cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            text=True, shell=False, start_new_session=True,
        )
        stdout.close(); stderr.close()
    except GpuUnavailableError as exc:
        return _finalize_failure(run_dir, attempt, "TEMPORARY_GPU_UNAVAILABLE", str(exc))
    except Exception as exc:
        return _finalize_failure(run_dir, attempt, "PROCESS_SPAWN_FAILED", str(exc))
    _PROCESSES[(str(run_dir.resolve()), attempt.attempt_id)] = process
    attempt = store.mark_running(run_dir, attempt_id=attempt.attempt_id, pid=process.pid, process_group_id=os.getpgid(process.pid))
    _write_json(output_dir / "process.json", {"pid": process.pid, "process_group_id": os.getpgid(process.pid), "started_at": _utc_now()})
    _write_heartbeat(output_dir, attempt, "running")
    append_event(run_dir, "experiment.attempt.running", {"attempt_id": attempt.attempt_id, "pid": process.pid})
    return AttemptObservation(terminal=False)


def observe_attempt_job(run_dir: Path, job: dict[str, Any]) -> AttemptObservation:
    attempt, _ = _load_attempt(run_dir, job)
    if attempt.runtime_status not in {"STARTING", "RUNNING", "TERMINATING"}:
        return AttemptObservation(terminal=attempt.runtime_status in {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"}, succeeded=attempt.runtime_status == "COMPLETED")
    output_dir = run_dir / "attempts" / attempt.attempt_id
    if attempt.termination_requested_at and attempt.termination_reason:
        _begin_or_escalate_termination(run_dir, attempt, attempt.termination_reason)
    checkpoint = output_dir / attempt.checkpoint_watch_path if attempt.checkpoint_watch_path else None
    health_events = RuntimeWatchdog().inspect(output_dir, pid=attempt.pid, checkpoint_path=checkpoint, checkpoint_stall_seconds=attempt.checkpoint_stall_seconds)
    event_names = {event.event for event in health_events}
    if "OOM_DETECTED" in event_names:
        _begin_or_escalate_termination(run_dir, attempt, "OOM")
        return AttemptObservation(terminal=False)
    if "NAN_OR_INF" in event_names:
        _begin_or_escalate_termination(run_dir, attempt, "NAN_OR_INF")
        return AttemptObservation(terminal=False)
    process = _PROCESSES.get((str(run_dir.resolve()), attempt.attempt_id))
    if attempt.cancel_requested_at:
        if attempt.termination_requested_at is None:
            _begin_or_escalate_termination(run_dir, attempt, "USER_CANCELLED")
            return AttemptObservation(terminal=False)
    if _timed_out(output_dir, attempt.job_timeout_sec):
        if attempt.termination_requested_at is None:
            _begin_or_escalate_termination(run_dir, attempt, "RUN_TIMEOUT")
            return AttemptObservation(terminal=False)
    if process is not None:
        code = process.poll()
        if code is None:
            ExperimentAttemptStore().heartbeat(run_dir, attempt_id=attempt.attempt_id)
            _write_heartbeat(output_dir, attempt, "running")
            return AttemptObservation(terminal=False)
        _PROCESSES.pop((str(run_dir.resolve()), attempt.attempt_id), None)
        if attempt.termination_reason:
            status = "CANCELLED" if attempt.termination_reason == "USER_CANCELLED" else "TIMED_OUT" if attempt.termination_reason == "RUN_TIMEOUT" else "FAILED"
            return _finalize_failure(run_dir, attempt, attempt.termination_reason, "process terminated by runtime policy", runtime_status=status, exit_code=code, timed_out=status == "TIMED_OUT")
        return _finalize_exit(run_dir, attempt, code)
    if _pid_alive(attempt.pid):
        ExperimentAttemptStore().heartbeat(run_dir, attempt_id=attempt.attempt_id)
        _write_heartbeat(output_dir, attempt, "running")
        return AttemptObservation(terminal=False)
    return _finalize_failure(run_dir, attempt, "WORKER_LOST", "process exited after Worker restart", runtime_status="LOST")


def _finalize_exit(run_dir: Path, attempt, exit_code: int) -> AttemptObservation:
    output_dir = run_dir / "attempts" / attempt.attempt_id
    if exit_code != 0:
        return _finalize_failure(run_dir, attempt, "RUN_COMMAND_FAILED", f"experiment command exited with code {exit_code}", exit_code=exit_code)
    missing = [path for path in attempt.command_plan.expected_outputs if not (output_dir / path).is_file()]
    if missing:
        return _finalize_failure(run_dir, attempt, "RUN_EXPECTED_OUTPUT_MISSING", f"missing expected outputs: {missing}", exit_code=exit_code)
    entries = [OutputManifestEntry(path=path, sha256=sha256_file(output_dir / path), size_bytes=(output_dir / path).stat().st_size) for path in attempt.command_plan.expected_outputs]
    manifest_data = {"schema_version": 1, "outputs": [entry.model_dump(mode="json") for entry in entries]}
    manifest_data["manifest_sha256"] = canonical_sha256(manifest_data)
    _write_json(output_dir / "output_manifest.json", manifest_data)
    result = ExperimentExecutionResult(schema_version=1, run_id=attempt.run_id, attempt=attempt.attempt_id, command_id=attempt.command_plan.command_id, command_sha256=attempt.input_refs.command_sha256, status="success", exit_code=0, timed_out=False, stdout_path="stdout.log", stderr_path="stderr.log", output_manifest_path="output_manifest.json")
    _write_json(output_dir / "execution_result.json", result.model_dump(mode="json", exclude_none=True))
    return _finalize(run_dir, attempt, result, "COMPLETED")


def _finalize_failure(run_dir: Path, attempt, code: str, message: str, *, runtime_status: str = "FAILED", exit_code: int | None = None, timed_out: bool = False) -> AttemptObservation:
    output_dir = run_dir / "attempts" / attempt.attempt_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "stdout.log").touch(exist_ok=True); (output_dir / "stderr.log").touch(exist_ok=True)
    result = ExperimentExecutionResult(schema_version=1, run_id=attempt.run_id, attempt=attempt.attempt_id, command_id=attempt.command_plan.command_id, command_sha256=attempt.input_refs.command_sha256, status="execution_failed", exit_code=exit_code, timed_out=timed_out, stdout_path="stdout.log", stderr_path="stderr.log", failure_code=code, failure_message=message)
    _write_json(output_dir / "execution_result.json", result.model_dump(mode="json", exclude_none=True))
    return _finalize(run_dir, attempt, result, runtime_status)


def _finalize(run_dir: Path, attempt, result: ExperimentExecutionResult, runtime_status: str) -> AttemptObservation:
    store = ExperimentAttemptStore()
    final = store.finish(run_dir, attempt_id=attempt.attempt_id, runtime_status=runtime_status, failure_code=result.failure_code, execution_result_ref=f"attempts/{attempt.attempt_id}/execution_result.json")
    if runtime_status != "COMPLETED": classify_or_load(run_dir / "attempts" / attempt.attempt_id)
    finalize_attempt(run_dir / "attempts" / attempt.attempt_id, attempt_id=attempt.attempt_id, runtime_status=runtime_status, run_dir=run_dir, evaluation_contract_ref=attempt.evaluation_contract_ref, evaluation_contract_sha256=attempt.evaluation_contract_sha256, protected_artifact_report_ref=attempt.protected_artifact_report_ref, protected_artifact_report_sha256=attempt.protected_artifact_report_sha256)
    if RetryPolicy().should_retry(final):
        from autoad_researcher.experiment.attempt_service import ExperimentAttemptService
        ExperimentAttemptService().create_retry(run_dir, attempt_id=final.attempt_id)
    elif final.failure_code in {None, "UNKNOWN_RUN_FAILURE"}:
        events = _health_event_names(run_dir / "attempts" / attempt.attempt_id / "health_events.jsonl")
        diagnosis = HealthDiagnosisAgent().diagnose(failure_code=final.failure_code, health_events=events)
        if diagnosis is not None:
            _write_json(run_dir / "attempts" / attempt.attempt_id / "health_diagnosis.json", diagnosis.model_dump(mode="json"))
    if attempt.resource_lease_id:
        try: GpuAllocator().release(run_dir, lease_id=attempt.resource_lease_id, worker_id=_worker_id())
        except (FileNotFoundError, ValueError): pass
    append_event(run_dir, "experiment.attempt.finalized", {"attempt_id": final.attempt_id, "runtime_status": final.runtime_status, "failure_code": final.failure_code})
    return AttemptObservation(terminal=True, succeeded=final.runtime_status == "COMPLETED", outputs=_outputs(run_dir, run_dir / "attempts" / attempt.attempt_id), error=result.failure_message)


def _load_attempt(run_dir: Path, job: dict[str, Any]):
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    attempt_id = _required_string(payload, "attempt_id"); job_id = _required_string(job, "job_id")
    attempt = ExperimentAttemptStore().load(run_dir, attempt_id)
    if attempt is None: raise FileNotFoundError("experiment Attempt not found")
    return attempt, job_id


def _resolve_run_relative_path(run_dir: Path, relative_path: str) -> Path:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part == ".." for part in path.parts): raise ValueError("Attempt command cwd must stay within the run directory")
    resolved = run_dir.joinpath(*path.parts).resolve()
    if not resolved.is_relative_to(run_dir.resolve()): raise ValueError("Attempt command cwd escapes the run directory")
    return resolved


def _timed_out(output_dir: Path, timeout: int) -> bool:
    path = output_dir / "process.json"
    if not path.is_file(): return False
    try: started = datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["started_at"])
    except (KeyError, ValueError, json.JSONDecodeError): return False
    return (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds() > timeout


def _pid_alive(pid: int | None) -> bool:
    if pid is None: return False
    try: os.kill(pid, 0)
    except OSError: return False
    return True


def _kill_process_group(process_group_id: int | None) -> None:
    if process_group_id is None: return
    try: os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError: return

def _begin_or_escalate_termination(run_dir: Path, attempt, reason: str) -> None:
    current = datetime.now(timezone.utc)
    if attempt.termination_requested_at is None:
        updated = ExperimentAttemptStore().request_termination(run_dir, attempt_id=attempt.attempt_id, reason=reason)
        _kill_process_group(updated.process_group_id)
        return
    requested = datetime.fromisoformat(attempt.termination_requested_at)
    if (current - requested.astimezone(timezone.utc)).total_seconds() >= attempt.termination_grace_seconds:
        if attempt.process_group_id is not None:
            try: os.killpg(attempt.process_group_id, signal.SIGKILL)
            except ProcessLookupError: pass


def _write_heartbeat(output_dir: Path, attempt, status: str) -> None:
    _write_json(output_dir / "heartbeat.json", {"pid": attempt.pid, "status": status, "step": None, "epoch": None, "loss": None, "last_metric": None, "timestamp": _utc_now()})


def _outputs(run_dir: Path, output_dir: Path) -> list[str]: return [str(path.relative_to(run_dir)) for path in sorted(output_dir.iterdir()) if path.is_file()]
def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value: raise ValueError(f"experiment Attempt Job requires {key}")
    return value
def _worker_id() -> str: return f"worker-{os.uname().nodename}-{os.getpid()}"
def _utc_now() -> str: return datetime.now(timezone.utc).isoformat()
def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def _health_event_names(path: Path) -> list[str]:
    if not path.is_file(): return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try: names.append(str(json.loads(line).get("event")))
        except json.JSONDecodeError: continue
    return names
