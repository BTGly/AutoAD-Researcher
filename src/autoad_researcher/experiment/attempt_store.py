"""Atomic persistence and state transitions for ExperimentAttempt records."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.experiment.attempt import AttemptRuntimeStatus, ExperimentAttempt
from autoad_researcher.runner.executor import experiment_command_sha256

ATTEMPTS_DIR = "experiments/attempts"


class ExperimentAttemptStore:
    """Keep idempotency, IDs, and runtime transitions behind one file lock."""

    def create_or_get(self, run_dir: Path, candidate: ExperimentAttempt) -> tuple[ExperimentAttempt, bool]:
        with self._lock(run_dir):
            existing = self._find_by_idempotency_key_unlocked(run_dir, candidate.idempotency_key)
            if existing is not None:
                if self._identity(existing) != self._identity(candidate):
                    raise ValueError("same idempotency key, different Attempt identity")
                return existing, False
            attempt_id = self._next_attempt_id_unlocked(run_dir)
            created = candidate.model_copy(update={"attempt_id": attempt_id})
            self._write_unlocked(self._path(run_dir, attempt_id), created)
            return created, True

    def load(self, run_dir: Path, attempt_id: str) -> ExperimentAttempt | None:
        path = self._path(run_dir, attempt_id)
        if not path.is_file():
            return None
        return ExperimentAttempt.model_validate_json(path.read_text(encoding="utf-8"))

    def bind_pipeline_job(
        self,
        run_dir: Path,
        *,
        attempt_id: str,
        pipeline_job_id: str,
    ) -> ExperimentAttempt:
        return self._update(
            run_dir,
            attempt_id,
            lambda attempt: self._ensure_pipeline_job(attempt, pipeline_job_id),
        )

    def mark_starting(self, run_dir: Path, *, attempt_id: str, pipeline_job_id: str) -> ExperimentAttempt:
        def mutate(attempt: ExperimentAttempt) -> ExperimentAttempt:
            if attempt.pipeline_job_id != pipeline_job_id:
                raise ValueError("PipelineJob does not belong to Attempt")
            if attempt.runtime_status not in {"QUEUED", "STARTING"}:
                raise ValueError("Attempt cannot be claimed from its current runtime status")
            return attempt.model_copy(update={"runtime_status": "STARTING"})

        return self._update(run_dir, attempt_id, mutate)

    def bind_resource_lease(self, run_dir: Path, *, attempt_id: str, lease_id: str) -> ExperimentAttempt:
        def mutate(attempt: ExperimentAttempt) -> ExperimentAttempt:
            if attempt.required_device_count == 0:
                raise ValueError("Attempt did not request GPU resources")
            if attempt.resource_lease_id is not None and attempt.resource_lease_id != lease_id:
                raise ValueError("Attempt is already bound to another ResourceLease")
            return attempt.model_copy(update={"resource_lease_id": lease_id})

        return self._update(run_dir, attempt_id, mutate)

    def finish(
        self,
        run_dir: Path,
        *,
        attempt_id: str,
        runtime_status: AttemptRuntimeStatus,
        failure_code: str | None,
        execution_result_ref: str,
    ) -> ExperimentAttempt:
        if runtime_status not in {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"}:
            raise ValueError("finish requires a terminal Attempt runtime status")
        if runtime_status == "COMPLETED" and failure_code is not None:
            raise ValueError("completed Attempt must not have a failure code")

        def mutate(attempt: ExperimentAttempt) -> ExperimentAttempt:
            if attempt.runtime_status in {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"}:
                if (
                    attempt.runtime_status == runtime_status
                    and attempt.failure_code == failure_code
                    and attempt.execution_result_ref == execution_result_ref
                ):
                    return attempt
                raise ValueError("Attempt was already finalized differently")
            exhausted = runtime_status in {"FAILED", "TIMED_OUT", "LOST"} and attempt.retry_count >= attempt.max_retries
            return attempt.model_copy(
                update={
                    "runtime_status": runtime_status,
                    "failure_code": failure_code,
                    "execution_result_ref": execution_result_ref,
                    "retry_exhausted": exhausted,
                }
            )

        return self._update(run_dir, attempt_id, mutate)

    def create_retry_candidate(
        self,
        run_dir: Path,
        *,
        attempt_id: str,
        created_at: str,
    ) -> ExperimentAttempt:
        with self._lock(run_dir):
            parent = self._load_required_unlocked(run_dir, attempt_id)
            if parent.runtime_status not in {"FAILED", "TIMED_OUT", "LOST"}:
                raise ValueError("only failed terminal Attempts may be retried")
            if parent.retry_count >= parent.max_retries:
                self._write_unlocked(
                    self._path(run_dir, parent.attempt_id),
                    self._touch(parent.model_copy(update={"retry_exhausted": True})),
                )
                raise ValueError("Attempt retry limit exhausted")
            retry_count = parent.retry_count + 1
            retry_key = f"retry:{parent.attempt_id}:{retry_count}"
            existing = self._find_by_idempotency_key_unlocked(run_dir, retry_key)
            if existing is not None:
                return existing
            delay_seconds = min(60, 5 * (2 ** parent.retry_count))
            retry_attempt_id = self._next_attempt_id_unlocked(run_dir)
            retry_plan = parent.command_plan
            if retry_plan.cwd == f"attempts/{parent.attempt_id}":
                retry_plan = retry_plan.model_copy(update={"cwd": f"attempts/{retry_attempt_id}"})
            retry_input_refs = parent.input_refs.model_copy(
                update={"command_sha256": experiment_command_sha256(retry_plan)}
            )
            retry = parent.model_copy(
                update={
                    "attempt_id": retry_attempt_id,
                    "idempotency_key": retry_key,
                    "pipeline_job_id": None,
                    "runtime_status": "QUEUED",
                    "pid": None,
                    "process_group_id": None,
                    "resource_lease_id": None,
                    "heartbeat_at": None,
                    "cancel_requested_at": None,
                    "retry_of": parent.attempt_id,
                    "retry_count": retry_count,
                    "command_plan": retry_plan,
                    "input_refs": retry_input_refs,
                    "retry_not_before": _utc_after(delay_seconds),
                    "failure_code": None,
                    "retry_exhausted": False,
                    "execution_result_ref": None,
                    "created_at": created_at,
                    "updated_at": created_at,
                    "revision": 0,
                }
            )
            self._write_unlocked(self._path(run_dir, retry.attempt_id), retry)
            return retry

    @staticmethod
    def _identity(attempt: ExperimentAttempt) -> dict[str, object]:
        return {
            "run_id": attempt.run_id,
            "session_id": attempt.session_id,
            "job_type": attempt.job_type,
            "attempt_purpose": attempt.attempt_purpose,
            "command_plan": attempt.command_plan.model_dump(mode="json"),
            "input_refs": attempt.input_refs.model_dump(mode="json"),
            "job_timeout_sec": attempt.job_timeout_sec,
            "max_retries": attempt.max_retries,
            "required_device_count": attempt.required_device_count,
            "required_vram_mb": attempt.required_vram_mb,
            "retry_of": attempt.retry_of,
            "retry_count": attempt.retry_count,
        }

    @staticmethod
    def _ensure_pipeline_job(attempt: ExperimentAttempt, pipeline_job_id: str) -> ExperimentAttempt:
        if attempt.pipeline_job_id is not None and attempt.pipeline_job_id != pipeline_job_id:
            raise ValueError("Attempt is already bound to another PipelineJob")
        return attempt.model_copy(update={"pipeline_job_id": pipeline_job_id})

    def _update(self, run_dir: Path, attempt_id: str, mutate) -> ExperimentAttempt:
        with self._lock(run_dir):
            attempt = self._load_required_unlocked(run_dir, attempt_id)
            updated = mutate(attempt)
            if updated == attempt:
                return attempt
            updated = self._touch(updated)
            self._write_unlocked(self._path(run_dir, attempt_id), updated)
            return updated

    @staticmethod
    def _touch(attempt: ExperimentAttempt) -> ExperimentAttempt:
        return attempt.model_copy(update={"updated_at": _utc_now(), "revision": attempt.revision + 1})

    def _find_by_idempotency_key_unlocked(self, run_dir: Path, key: str) -> ExperimentAttempt | None:
        directory = run_dir / ATTEMPTS_DIR
        if not directory.is_dir():
            return None
        for path in sorted(directory.glob("attempt_*.json")):
            candidate = ExperimentAttempt.model_validate_json(path.read_text(encoding="utf-8"))
            if candidate.idempotency_key == key:
                return candidate
        return None

    def _next_attempt_id_unlocked(self, run_dir: Path) -> str:
        max_number = 0
        directory = run_dir / ATTEMPTS_DIR
        if directory.is_dir():
            for path in directory.glob("attempt_*.json"):
                try:
                    max_number = max(max_number, int(path.stem.removeprefix("attempt_")))
                except ValueError:
                    continue
        return f"attempt_{max_number + 1:06d}"

    def _load_required_unlocked(self, run_dir: Path, attempt_id: str) -> ExperimentAttempt:
        path = self._path(run_dir, attempt_id)
        if not path.is_file():
            raise FileNotFoundError("experiment Attempt not found")
        return ExperimentAttempt.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _path(run_dir: Path, attempt_id: str) -> Path:
        return run_dir / ATTEMPTS_DIR / f"{attempt_id}.json"

    @staticmethod
    def _write_unlocked(path: Path, attempt: ExperimentAttempt) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(attempt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, timeout: float = 5.0):
        lock_path = run_dir / ATTEMPTS_DIR / ".attempts.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        fd = None
        while time.monotonic() < deadline:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                time.sleep(0.05)
        if fd is None:
            raise TimeoutError(f"Could not acquire Attempt lock for {run_dir} within {timeout}s")
        try:
            yield
        finally:
            os.close(fd)
            try:
                os.unlink(lock_path)
            except OSError:
                pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_after(delay_seconds: int) -> str:
    return datetime.fromtimestamp(time.time() + delay_seconds, tz=timezone.utc).isoformat()
