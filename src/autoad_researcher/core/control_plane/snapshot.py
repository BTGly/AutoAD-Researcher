"""Consistent read snapshots for experiment control APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.core.control_plane.experiment_state import load_session_unlocked
from autoad_researcher.core.control_plane.job_store import PipelineJobStore
from autoad_researcher.core.control_plane.lock import RunMutationLock
from autoad_researcher.core.control_plane.materialization_requests import (
    MaterializationRequestStore,
)
from autoad_researcher.core.control_plane.readiness import load_readiness_unlocked


def load_experiment_control_snapshot(run_dir: Path) -> dict[str, Any]:
    """Load Session, Job, Readiness, and requests under one shared run lock."""
    with RunMutationLock(run_dir, mode="shared"):
        session = load_session_unlocked(run_dir)
        jobs = PipelineJobStore(run_dir)._load_unlocked()
        job = (
            next((item for item in jobs if item.job_id == session.prepare_job_id), None)
            if session is not None
            else None
        )
        readiness = load_readiness_unlocked(run_dir)
        requests = MaterializationRequestStore(run_dir)._load_unlocked()
        return {
            "session": session,
            "job": job,
            "readiness": readiness,
            "requests": requests,
        }
