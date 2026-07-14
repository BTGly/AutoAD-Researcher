from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autoad_researcher.assistant.v2.contract_confirmation_service import (
    decide_contract_confirmation as decide_contract_confirmation_saga,
)
from autoad_researcher.assistant.v2.draft_service import load_research_draft_state
from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.server.config import RUNS_ROOT

router = APIRouter(prefix="/api/runs", tags=["draft"])


class ContractConfirmationDecision(BaseModel):
    confirmation_id: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]


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
            "confirmation": None,
            "advisory_enrichment": [],
        }
    return load_research_draft_state(run_dir)


@router.post("/{run_id}/draft/confirmation")
async def decide_contract_confirmation(run_id: str, request: ContractConfirmationDecision):
    try:
        run_dir = run_dir_path(RUNS_ROOT, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run not found")

    try:
        result = decide_contract_confirmation_saga(
            run_dir,
            confirmation_id=request.confirmation_id,
            decision=request.decision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result["message"] = (
        "研究任务合同已确认。" if request.decision == "approved" else "已返回继续修改研究任务合同。"
    )
    return result
