"""Recover Coordinator cache state from the durable experiment control plane."""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.job_service import create_or_get_pipeline_job, load_pipeline_jobs
from autoad_researcher.experiment.attempt import ExperimentAttempt
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.cognition import CognitiveCommitStore, CoordinatorRecovery
from autoad_researcher.experiment.idea_tree import IdeaTreeStore

COORDINATOR_CHECKPOINT_DIR = "experiments/coordinator"
_PENDING_ATTEMPT_STATUSES = {"QUEUED", "STARTING", "RUNNING", "TERMINATING"}


class CoordinatorCheckpoint(BaseModel):
    """Metadata for an externally saved DeepAgents checkpoint, never authority state."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    checkpoint_ref: str = Field(min_length=1)
    tree_revision: int = Field(ge=0)
    created_at: str


class PendingAttemptReconnect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    disposition: Literal["already_connected", "repaired", "requires_observation"]
    pipeline_job_id: str | None = None


class CoordinatorRecoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_action: Literal["resume_checkpoint", "rebuild_from_authority"]
    checkpoint_reason: str
    observation_recovery: CoordinatorRecovery
    pending_attempts: list[PendingAttemptReconnect]


class CoordinatorCheckpointStore:
    """Persist only checkpoint identity and the Tree revision it summarized."""

    def write(self, run_dir: Path, *, session_id: str, checkpoint: CoordinatorCheckpoint) -> Path:
        path = self._path(run_dir, session_id)
        with self._lock(run_dir, session_id):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            try:
                with temporary.open("w", encoding="utf-8") as handle:
                    handle.write(json.dumps(checkpoint.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        append_event(run_dir, "experiment.coordinator.checkpoint.recorded", {"session_id": session_id, **checkpoint.model_dump(mode="json")})
        return path

    def load(self, run_dir: Path, *, session_id: str) -> CoordinatorCheckpoint | None:
        path = self._path(run_dir, session_id)
        return CoordinatorCheckpoint.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    @staticmethod
    def _path(run_dir: Path, session_id: str) -> Path:
        return run_dir / COORDINATOR_CHECKPOINT_DIR / session_id / "checkpoint.json"

    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, session_id: str, timeout: float = 5.0):
        path = run_dir / COORDINATOR_CHECKPOINT_DIR / session_id / ".checkpoint.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        fd: int | None = None
        while time.monotonic() < deadline:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                time.sleep(0.05)
        if fd is None:
            raise TimeoutError("could not acquire Coordinator checkpoint lock")
        try:
            yield
        finally:
            os.close(fd)
            try:
                path.unlink()
            except OSError:
                pass


class CoordinatorRecoveryService:
    """Choose cache reuse only when it agrees with the authoritative IdeaTree."""

    def __init__(self, *, checkpoint_store: CoordinatorCheckpointStore | None = None, tree_store: IdeaTreeStore | None = None, commit_store: CognitiveCommitStore | None = None, attempt_store: ExperimentAttemptStore | None = None):
        self._checkpoints = checkpoint_store or CoordinatorCheckpointStore()
        self._trees = tree_store or IdeaTreeStore()
        self._commits = commit_store or CognitiveCommitStore()
        self._attempts = attempt_store or ExperimentAttemptStore()

    def recover(self, run_dir: Path, *, session_id: str) -> CoordinatorRecoveryResult:
        tree = self._trees.load(run_dir, session_id=session_id)
        if tree is None:
            raise FileNotFoundError("Coordinator recovery requires IdeaTree")
        checkpoint = self._checkpoints.load(run_dir, session_id=session_id)
        if checkpoint is None:
            checkpoint_action, checkpoint_reason = "rebuild_from_authority", "DeepAgents checkpoint metadata is missing"
        elif checkpoint.tree_revision != tree.revision:
            checkpoint_action, checkpoint_reason = "rebuild_from_authority", "DeepAgents checkpoint tree revision is stale"
        else:
            checkpoint_action, checkpoint_reason = "resume_checkpoint", "DeepAgents checkpoint matches current IdeaTree revision"
        observation_recovery = self._commits.recovery(run_dir, session_id=session_id, tree_revision=tree.revision)
        pending = [
            self._reconnect_attempt(run_dir, attempt)
            for attempt in self._attempts.list_for_session(run_dir, session_id=session_id)
            if attempt.runtime_status in _PENDING_ATTEMPT_STATUSES
        ]
        result = CoordinatorRecoveryResult(
            checkpoint_action=checkpoint_action,
            checkpoint_reason=checkpoint_reason,
            observation_recovery=observation_recovery,
            pending_attempts=pending,
        )
        append_event(run_dir, "experiment.coordinator.recovered", {"session_id": session_id, **result.model_dump(mode="json")})
        return result

    def _reconnect_attempt(self, run_dir: Path, attempt: ExperimentAttempt) -> PendingAttemptReconnect:
        jobs = {str(job.get("job_id")): job for job in load_pipeline_jobs(run_dir)}
        linked = jobs.get(attempt.pipeline_job_id or "")
        if linked is not None:
            if linked.get("status") in {"queued", "running"}:
                return PendingAttemptReconnect(attempt_id=attempt.attempt_id, disposition="already_connected", pipeline_job_id=attempt.pipeline_job_id)
            return PendingAttemptReconnect(attempt_id=attempt.attempt_id, disposition="requires_observation", pipeline_job_id=attempt.pipeline_job_id)
        job, created = create_or_get_pipeline_job(
            run_dir,
            source_id=attempt.session_id,
            job_type=attempt.job_type,
            evidence_role=attempt.job_type,
            idempotency_key=f"experiment_job:{attempt.attempt_id}",
            payload={"session_id": attempt.session_id, "attempt_id": attempt.attempt_id},
        )
        repaired = self._attempts.reconnect_pipeline_job(
            run_dir,
            attempt_id=attempt.attempt_id,
            missing_pipeline_job_id=attempt.pipeline_job_id,
            pipeline_job_id=str(job["job_id"]),
        )
        if created:
            append_event(run_dir, "experiment.attempt.reconnected", {"attempt_id": repaired.attempt_id, "job_id": repaired.pipeline_job_id})
        return PendingAttemptReconnect(attempt_id=attempt.attempt_id, disposition="repaired", pipeline_job_id=repaired.pipeline_job_id)


def new_checkpoint(*, checkpoint_ref: str, tree_revision: int) -> CoordinatorCheckpoint:
    return CoordinatorCheckpoint(checkpoint_ref=checkpoint_ref, tree_revision=tree_revision, created_at=datetime.now(timezone.utc).isoformat())
