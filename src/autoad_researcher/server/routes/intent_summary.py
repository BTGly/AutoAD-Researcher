from fastapi import APIRouter

from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    load_research_intent_summary,
)
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400


router = APIRouter(prefix="/api/runs", tags=["intent-summary"])


@router.get("/{run_id}/intent-summary")
async def get_intent_summary(run_id: str):
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    summary = (
        load_research_intent_summary(run_dir)
        if run_dir.exists()
        else None
    )
    return (summary or ResearchIntentSummary()).model_dump(mode="json")
