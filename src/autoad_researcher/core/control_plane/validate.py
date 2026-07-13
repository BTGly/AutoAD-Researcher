"""Read-only validation of canonical control-plane stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.core.control_plane.event_store import ControlPlaneEventStore
from autoad_researcher.core.control_plane.job_store import PipelineJobStore


def validate_control_plane_store(run_dir: Path) -> dict[str, Any]:
    events = ControlPlaneEventStore(run_dir).read_since()
    jobs = PipelineJobStore(run_dir).list()
    return {
        "run_id": run_dir.name,
        "valid": True,
        "event_count": len(events),
        "job_count": len(jobs),
    }
