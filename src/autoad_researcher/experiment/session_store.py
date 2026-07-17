"""Atomic storage for ExperimentSession records."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.experiment.session import (
    ExecutionMode,
    ExperimentAuthorization,
    ExperimentSession,
    ReadinessStatus,
    SessionStatus,
)

SESSIONS_DIR = "experiments/sessions"


class ExperimentSessionStore:
    """Create, load, and revise Sessions without replacing task identity."""

    def create_or_get(
        self,
        run_dir: Path,
        *,
        task_ref: str,
        task_hash: str,
        execution_mode: ExecutionMode,
        repository_ref: str | None = None,
        budget: dict[str, Any] | None = None,
    ) -> tuple[ExperimentSession, bool]:
        session_id = f"session_{task_hash[:16]}"
        path = self._session_path(run_dir, session_id)
        with self._lock(run_dir):
            if path.is_file():
                session = ExperimentSession.model_validate_json(path.read_text(encoding="utf-8"))
                if (
                    session.run_id != run_dir.name
                    or session.task_ref != task_ref
                    or session.task_hash != task_hash
                ):
                    raise ValueError("existing Session conflicts with task identity")
                return session, False

            now = _utc_now()
            session = ExperimentSession(
                session_id=session_id,
                run_id=run_dir.name,
                task_ref=task_ref,
                task_hash=task_hash,
                repository_ref=repository_ref,
                budget=budget or {},
                authorization=ExperimentAuthorization(
                    execution_mode=execution_mode,
                    confirmed_at=now,
                ),
                created_at=now,
                updated_at=now,
            )
            self._write_unlocked(path, session)
            return session, True

    def update_authorization(
        self,
        run_dir: Path,
        *,
        session_id: str,
        execution_mode: ExecutionMode,
    ) -> tuple[ExperimentSession, bool]:
        path = self._session_path(run_dir, session_id)
        with self._lock(run_dir):
            if not path.is_file():
                raise FileNotFoundError("experiment session not found")
            session = ExperimentSession.model_validate_json(path.read_text(encoding="utf-8"))
            if session.authorization.execution_mode == execution_mode:
                return session, False
            now = _utc_now()
            updated = session.model_copy(
                update={
                    "authorization": ExperimentAuthorization(
                        execution_mode=execution_mode,
                        confirmed_at=now,
                    ),
                    "authorization_revision": session.authorization_revision + 1,
                    "revision": session.revision + 1,
                    "updated_at": now,
                },
            )
            self._write_unlocked(path, updated)
            return updated, True

    def load(self, run_dir: Path, session_id: str) -> ExperimentSession | None:
        path = self._session_path(run_dir, session_id)
        if not path.is_file():
            return None
        return ExperimentSession.model_validate_json(path.read_text(encoding="utf-8"))

    def update_environment_state(
        self,
        run_dir: Path,
        *,
        session_id: str,
        status: SessionStatus,
        environment_status: str,
        readiness_status: ReadinessStatus | None = None,
        readiness_blockers: list[str] | None = None,
        repository_ref: str | None = None,
        environment_snapshot_ref: str | None = None,
    ) -> ExperimentSession:
        """Persist one monotonic control-plane transition and its evidence refs."""
        path = self._session_path(run_dir, session_id)
        with self._lock(run_dir):
            if not path.is_file():
                raise FileNotFoundError("experiment session not found")
            session = ExperimentSession.model_validate_json(path.read_text(encoding="utf-8"))
            updates: dict[str, Any] = {
                "status": status,
                "environment_status": environment_status,
                "updated_at": _utc_now(),
                "revision": session.revision + 1,
            }
            if readiness_status is not None:
                updates["readiness_status"] = readiness_status
            if readiness_blockers is not None:
                updates["readiness_blockers"] = readiness_blockers
            if repository_ref is not None:
                updates["repository_ref"] = repository_ref
            if environment_snapshot_ref is not None:
                updates["environment_snapshot_ref"] = environment_snapshot_ref
            updated = session.model_copy(update=updates)
            self._write_unlocked(path, updated)
            return updated

    @staticmethod
    def _session_path(run_dir: Path, session_id: str) -> Path:
        return run_dir / SESSIONS_DIR / f"{session_id}.json"

    @staticmethod
    def _write_unlocked(path: Path, session: ExperimentSession) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(session.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, timeout: float = 5.0):
        lock_path = run_dir / SESSIONS_DIR / ".sessions.lock"
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
            raise TimeoutError(f"Could not acquire sessions lock for {run_dir} within {timeout}s")
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
