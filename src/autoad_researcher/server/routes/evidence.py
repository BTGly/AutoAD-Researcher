from pathlib import Path

from fastapi import APIRouter, HTTPException

from autoad_researcher.assistant.v2.evidence_service import load_unusable_parsed_sources, load_usable_evidence
from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.server.config import RUNS_ROOT

router = APIRouter(prefix="/api/runs", tags=["evidence"])


@router.get("/{run_id}/evidence")
async def get_evidence(run_id: str):
    run_dir = _run_dir_or_400(run_id)
    if not run_dir.exists():
        return []
    return load_usable_evidence(run_dir)


@router.get("/{run_id}/evidence/state")
async def get_evidence_state(run_id: str):
    run_dir = _run_dir_or_400(run_id)
    if not run_dir.exists():
        return {"usable_evidence": [], "unusable_parsed_sources": []}
    return {
        "usable_evidence": load_usable_evidence(run_dir),
        "unusable_parsed_sources": load_unusable_parsed_sources(run_dir),
    }


def _run_dir_or_400(run_id: str) -> Path:
    try:
        return run_dir_path(RUNS_ROOT, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
