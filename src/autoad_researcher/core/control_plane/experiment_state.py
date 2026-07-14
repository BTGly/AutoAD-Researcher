"""Strict ExperimentSession projection helpers used while the run lock is held."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from autoad_researcher.core.control_plane.errors import CorruptAuthoritativeStore
from autoad_researcher.core.control_plane.io import atomic_write_json
from autoad_researcher.core.control_plane.models import ExperimentSession


SESSION_RELATIVE_PATH = "experiment_agents/session.json"
READINESS_RELATIVE_PATH = "experiment_agents/readiness.json"


def session_path(run_dir: Path) -> Path:
    return run_dir / "experiment_agents" / "session.json"


def load_session_unlocked(run_dir: Path) -> ExperimentSession | None:
    path = session_path(run_dir)
    if not path.is_file():
        return None
    try:
        session = ExperimentSession.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise CorruptAuthoritativeStore(f"invalid experiment session: {path}") from exc
    if session.run_id != run_dir.name:
        raise CorruptAuthoritativeStore(f"experiment session run_id mismatch: {path}")
    if session.readiness_path != READINESS_RELATIVE_PATH:
        raise CorruptAuthoritativeStore(f"experiment session readiness_path mismatch: {path}")
    return session


def write_session_unlocked(run_dir: Path, session: ExperimentSession) -> None:
    atomic_write_json(
        session_path(run_dir),
        session.model_dump(mode="json", exclude_none=True),
    )


def transition_session_if_present_unlocked(
    run_dir: Path,
    *,
    prepare_job_id: str,
    status: str,
    now: datetime,
    error: str | None = None,
) -> ExperimentSession | None:
    session = load_session_unlocked(run_dir)
    if session is None:
        return None
    if session.prepare_job_id != prepare_job_id:
        raise CorruptAuthoritativeStore(
            f"session prepare_job_id={session.prepare_job_id} does not match {prepare_job_id}"
        )
    updated = session.model_copy(update={"status": status, "updated_at": now, "error": error})
    write_session_unlocked(run_dir, updated)
    return updated
