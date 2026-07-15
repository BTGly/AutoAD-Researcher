from fastapi import APIRouter, HTTPException

from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    load_research_intent_summary,
)
from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.server.config import RUNS_ROOT


router = APIRouter(prefix="/api/runs", tags=["intent-summary"])


@router.get("/{run_id}/intent-summary")
async def get_intent_summary(run_id: str):
    try:
        run_dir = run_dir_path(RUNS_ROOT, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = (
        load_research_intent_summary(run_dir)
        if run_dir.exists()
        else None
    )
    return (summary or ResearchIntentSummary()).model_dump(mode="json")
