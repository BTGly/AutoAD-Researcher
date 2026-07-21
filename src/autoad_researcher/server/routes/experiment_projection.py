"""Read-only HTTP surface for the Experiment Observatory."""

from fastapi import APIRouter, HTTPException, Query

from autoad_researcher.assistant.v2.experiment_projection import (
    ExperimentProjection,
    SessionInventoryError,
    build_projection,
)
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400


router = APIRouter(prefix="/api/runs", tags=["experiment-projection"])


@router.get("/{run_id}/experiment/projection", response_model=ExperimentProjection)
async def get_experiment_projection(
    run_id: str,
    session_id: str | None = Query(default=None),
) -> ExperimentProjection:
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="run not found")
    try:
        return build_projection(run_dir, session_id=session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionInventoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
