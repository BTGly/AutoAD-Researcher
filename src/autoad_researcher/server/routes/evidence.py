from pathlib import Path

from fastapi import APIRouter

from autoad_researcher.assistant.v2.evidence_service import load_unusable_parsed_sources, load_usable_evidence
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400

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
    return run_dir_or_400(RUNS_ROOT, run_id)
