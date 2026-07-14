"""ExperimentSession, readiness, materialization, and retry APIs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autoad_researcher.core.control_plane import (
    CorruptAuthoritativeStore,
    IdempotencyConflict,
    PipelineJobStore,
)
from autoad_researcher.core.control_plane.materialization_requests import (
    MaterializationRequestStore,
)
from autoad_researcher.core.control_plane.readiness import (
    ensure_experiment_session,
    load_experiment_readiness,
    load_experiment_session,
)
from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.server.config import RUNS_ROOT


router = APIRouter(prefix="/api/runs", tags=["experiment-control"])


class MaterializationCommand(BaseModel):
    request_id: str = Field(min_length=1)
    force: bool = False
    reason: str = Field(min_length=1)


@router.get("/{run_id}/experiment-session")
async def get_experiment_session(run_id: str):
    run_dir = _run_dir(run_id)
    session = load_experiment_session(run_dir)
    if session is None:
        return {"session": None, "readiness": None, "job": None, "requests": []}
    job = PipelineJobStore(run_dir).get(session.prepare_job_id)
    if job is None:
        raise HTTPException(status_code=500, detail="ExperimentSession prepare job is missing")
    readiness = load_experiment_readiness(run_dir)
    return {
        "session": session.model_dump(mode="json", exclude_none=True),
        "readiness": (
            readiness.model_dump(mode="json", exclude_none=True)
            if readiness is not None
            else None
        ),
        "job": job.model_dump(mode="json", exclude_none=False),
        "requests": [
            record.model_dump(mode="json", exclude_none=True)
            for record in MaterializationRequestStore(run_dir).list()
        ],
    }


@router.post("/{run_id}/experiment-session/materialize")
async def request_experiment_materialization(run_id: str, request: MaterializationCommand):
    return _schedule(run_id, request, require_failed=False)


@router.post("/{run_id}/experiment-session/retry")
async def retry_experiment_materialization(run_id: str, request: MaterializationCommand):
    return _schedule(run_id, request, require_failed=True)


def _schedule(
    run_id: str,
    request: MaterializationCommand,
    *,
    require_failed: bool,
):
    run_dir = _run_dir(run_id)
    try:
        ensure_experiment_session(run_dir)
        record = MaterializationRequestStore(run_dir).request(
            request_id=request.request_id,
            force=request.force,
            reason=request.reason,
            require_failed=require_failed,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, CorruptAuthoritativeStore) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    payload = record.model_dump(mode="json", exclude_none=True)
    if record.action == "not_scheduled":
        raise HTTPException(status_code=409, detail=payload)
    return payload


def _run_dir(run_id: str):
    try:
        run_dir = run_dir_path(RUNS_ROOT, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run not found")
    return run_dir
