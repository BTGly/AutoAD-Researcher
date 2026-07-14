"""Read-only validation of canonical control-plane stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.core.control_plane.event_store import ControlPlaneEventStore
from autoad_researcher.core.control_plane.job_store import PipelineJobStore


def validate_control_plane_store(run_dir: Path) -> dict[str, Any]:
    events = ControlPlaneEventStore(run_dir).read_since()
    result = validate_authoritative_control_plane_store(run_dir)
    return {
        "run_id": run_dir.name,
        "valid": True,
        "event_count": len(events),
        "job_count": result["job_count"],
    }


def validate_authoritative_control_plane_store(run_dir: Path) -> dict[str, Any]:
    jobs = PipelineJobStore(run_dir).list()
    from autoad_researcher.core.control_plane.errors import CorruptAuthoritativeStore
    from autoad_researcher.core.control_plane.materialization_requests import (
        MaterializationRequestStore,
    )
    from autoad_researcher.core.control_plane.readiness import (
        load_experiment_readiness,
        load_experiment_session,
    )

    session = load_experiment_session(run_dir)
    readiness = load_experiment_readiness(run_dir)
    MaterializationRequestStore(run_dir).list()
    if session is not None and not any(job.job_id == session.prepare_job_id for job in jobs):
        raise CorruptAuthoritativeStore("ExperimentSession prepare job is missing")
    if readiness is not None and (
        session is None
        or readiness.session_id != session.session_id
        or readiness.contract_sha256 != session.contract_sha256
    ):
        raise CorruptAuthoritativeStore("ExperimentReadiness does not match ExperimentSession")
    return {"run_id": run_dir.name, "valid": True, "job_count": len(jobs)}
