from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    load_research_intent_summary,
)
from autoad_researcher.assistant.v2.task_bridge import (
    ExperimentTaskDraft,
    TaskBridge,
    TaskConfirmationConflict,
)
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400


router = APIRouter(prefix="/api/runs", tags=["intent-summary"])


class ConfirmPrimaryMetricsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    primary_metrics: list[str] = Field(min_length=1)

    @field_validator("primary_metrics")
    @classmethod
    def _require_non_empty_metrics(cls, values: list[str]) -> list[str]:
        if any(not value for value in values):
            raise ValueError("primary metrics must not contain empty values")
        return values


@router.get("/{run_id}/intent-summary")
async def get_intent_summary(run_id: str):
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    summary = (
        load_research_intent_summary(run_dir)
        if run_dir.exists()
        else None
    )
    return (summary or ResearchIntentSummary()).model_dump(mode="json")


@router.put("/{run_id}/intent-summary/primary-metrics", response_model=ExperimentTaskDraft)
async def confirm_primary_metrics(run_id: str, request: ConfirmPrimaryMetricsRequest):
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="run not found")
    try:
        return TaskBridge.confirm_primary_metrics(
            run_dir,
            primary_metrics=request.primary_metrics,
        )
    except TaskConfirmationConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
