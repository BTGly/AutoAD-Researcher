from fastapi import APIRouter, HTTPException

from autoad_researcher.assistant.v2.draft_service import load_research_draft_state
from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.server.config import RUNS_ROOT

router = APIRouter(prefix="/api/runs", tags=["draft"])


@router.get("/{run_id}/draft")
async def get_draft(run_id: str):
    try:
        run_dir = run_dir_path(RUNS_ROOT, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not run_dir.exists():
        return {
            "schema_version": 1,
            "ready": False,
            "has_draft": False,
            "title": "研究计划草案",
            "fields": [],
            "missing": [],
            "sources": [],
            "evidence": [],
            "jobs": [],
            "next_questions": [],
        }
    return load_research_draft_state(run_dir)
