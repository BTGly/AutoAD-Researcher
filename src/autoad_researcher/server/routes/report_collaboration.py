"""Read-only report discussion and explicit, user-confirmed follow-up actions."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.reporting.discussion import load_messages, load_turns, respond_to_turn, start_turn
from autoad_researcher.server.routes.chat import _extract_api_headers
from autoad_researcher.reporting.review import (
    PivotTaskContext,
    ProposalBudgetEstimate,
    confirm_proposal,
    create_proposal,
    record_review,
    reject_proposal,
)
from autoad_researcher.assistant.v2.experiment.candidate_control import CandidateLaunchInput
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400

router = APIRouter(prefix="/api/runs/{run_id}/reports/{report_id}", tags=["report-collaboration"])


class DiscussionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    content: str = Field(min_length=1, max_length=8000)
    evidence_ids: list[str] = Field(default_factory=list)


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_type: Literal["ADD_CONFIRMATION", "RETRY_FAILED", "REFINE_CURRENT", "PIVOT", "REQUEST_HUMAN"]
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    requested_changes: list[str] = Field(default_factory=list)
    required_experiments: list[str] = Field(default_factory=list)
    estimated_budget: ProposalBudgetEstimate | None = None
    unresolved_questions: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"
    target_attempt_id: str | None = Field(default=None, pattern=r"^attempt_[0-9]{6}$")
    candidate_attempt_id: str | None = Field(default=None, pattern=r"^attempt_[0-9]{6}$")
    noise_threshold: float | None = Field(default=None, ge=0)
    refine_input: CandidateLaunchInput | None = None
    pivot_context: PivotTaskContext | None = None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    decision: Literal["accept", "suspend", "needs_more", "needs_repair", "needs_pivot", "disputed"]
    user_comment: str = ""
    accepted_claims: list[str] = Field(default_factory=list)
    disputed_claims: list[str] = Field(default_factory=list)
    requested_follow_up: list[str] = Field(default_factory=list)


@router.get("/discussion")
async def get_discussion(run_id: str, report_id: str):
    try:
        root = run_dir_or_400(RUNS_ROOT, run_id)
        return {"turns": [item.model_dump(mode="json") for item in load_turns(root, report_id=report_id)], "messages": [item.model_dump(mode="json") for item in load_messages(root, report_id=report_id)]}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "report discussion not found") from exc


@router.post("/discussion")
async def post_discussion(run_id: str, report_id: str, request: DiscussionRequest, http_request: Request):
    try:
        # A discussion never invokes jobs or exposes filesystem/executor tools.
        root = run_dir_or_400(RUNS_ROOT, run_id)
        item = start_turn(root, report_id=report_id, request_id=request.request_id, content=request.content, evidence_ids=request.evidence_ids)
        api_key, provider_url, model = _extract_api_headers(http_request)
        item = respond_to_turn(root, report_id=report_id, turn_id=item.turn_id, api_key=api_key, provider_url=provider_url, model=model)
        return item.model_dump(mode="json")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/proposals")
async def post_proposal(run_id: str, report_id: str, request: ProposalRequest):
    try:
        return create_proposal(run_dir_or_400(RUNS_ROOT, run_id), report_id=report_id, **request.model_dump(mode="python")).model_dump(mode="json")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/proposals/{proposal_id}/confirm")
async def post_confirm_proposal(run_id: str, report_id: str, proposal_id: str):
    try:
        return confirm_proposal(run_dir_or_400(RUNS_ROOT, run_id), report_id=report_id, proposal_id=proposal_id).model_dump(mode="json")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/proposals/{proposal_id}/reject")
async def post_reject_proposal(run_id: str, report_id: str, proposal_id: str):
    try:
        return reject_proposal(run_dir_or_400(RUNS_ROOT, run_id), report_id=report_id, proposal_id=proposal_id).model_dump(mode="json")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/review-decision")
async def post_review(run_id: str, report_id: str, request: ReviewRequest):
    try:
        return record_review(run_dir_or_400(RUNS_ROOT, run_id), report_id=report_id, **request.model_dump(mode="python")).model_dump(mode="json")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
