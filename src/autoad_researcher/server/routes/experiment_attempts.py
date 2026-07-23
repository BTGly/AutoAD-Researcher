"""Product control routes for Session-owned experiment Attempts."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from autoad_researcher.assistant.v2.experiment.baseline_control import (
    BaselineContractInput,
    BaselineControlService,
    BaselineLaunchResult,
)
from autoad_researcher.assistant.v2.experiment.candidate_control import (
    CandidateControlService,
    CandidateLaunchInput,
    CandidateLaunchResult,
)
from autoad_researcher.assistant.v2.experiment.candidate_confirmation import (
    CandidateConfirmationInput,
    CandidateConfirmationResult,
    CandidateConfirmationService,
)
from autoad_researcher.assistant.v2.experiment.candidate_proposal import (
    CandidateProposalDecisionRequest,
    CandidateProposalGenerationRequest,
    CandidateProposalResult,
    CandidateProposalService,
    model_candidate_proposal_provider,
)
from autoad_researcher.assistant.v2.experiment.promotion_control import (
    PromotionControlService,
    PromotionInput,
    PromotionResult,
)
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400
from autoad_researcher.server.routes.chat import _extract_api_headers, _extract_role_route


router = APIRouter(prefix="/api/runs", tags=["experiment-attempts"])


class StartBaselineRequest(BaseModel):
    """Only user-confirmed scientific choices; execution details remain server-owned."""

    model_config = ConfigDict(extra="forbid")

    contract: BaselineContractInput


class StartCandidateRequest(BaseModel):
    """The reviewed candidate change, not a raw execution plan."""

    model_config = ConfigDict(extra="forbid")

    candidate: CandidateLaunchInput


@router.get("/{run_id}/sessions/{session_id}")
async def get_experiment_session(run_id: str, session_id: str):
    run_dir = _run_dir(run_id)
    session = ExperimentSessionStore().load(run_dir, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="experiment session not found")
    return session


@router.post(
    "/{run_id}/sessions/{session_id}/baseline",
    response_model=BaselineLaunchResult,
)
async def start_baseline(run_id: str, session_id: str, request: StartBaselineRequest):
    run_dir = _run_dir(run_id)
    try:
        return BaselineControlService().start(
            run_dir,
            session_id=session_id,
            contract_input=request.contract,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        code = (
            "execution_contract_incomplete" if message.startswith("execution_contract_incomplete:")
            else "idempotency_conflict" if message.startswith("idempotency_conflict:")
            else "baseline_start_invalid"
        )
        raise HTTPException(status_code=409, detail={"code": code, "message": message}) from exc


@router.post(
    "/{run_id}/sessions/{session_id}/candidates",
    response_model=CandidateLaunchResult,
)
async def start_candidate(run_id: str, session_id: str, request: StartCandidateRequest):
    run_dir = _run_dir(run_id)
    try:
        return CandidateControlService().start(run_dir, session_id=session_id, value=request.candidate)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        code = (
            "execution_contract_incomplete" if message.startswith("execution_contract_incomplete:")
            else "idempotency_conflict" if message.startswith("idempotency_conflict:")
            else "candidate_start_invalid"
        )
        raise HTTPException(status_code=409, detail={"code": code, "message": message}) from exc


@router.post(
    "/{run_id}/sessions/{session_id}/candidate-confirmations",
    response_model=CandidateConfirmationResult,
)
async def confirm_candidate(run_id: str, session_id: str, request: CandidateConfirmationInput):
    run_dir = _run_dir(run_id)
    try:
        return CandidateConfirmationService().start(run_dir, session_id=session_id, value=request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        code = "idempotency_conflict" if message.startswith("idempotency_conflict:") else "candidate_confirmation_invalid"
        raise HTTPException(status_code=409, detail={"code": code, "message": message}) from exc


@router.post(
    "/{run_id}/sessions/{session_id}/candidate-proposals",
    response_model=CandidateProposalResult,
)
async def generate_candidate_proposal(
    run_id: str,
    session_id: str,
    request: CandidateProposalGenerationRequest,
    http_request: Request,
):
    run_dir = _run_dir(run_id)
    route = _extract_role_route(http_request, "experiment_agent")
    api_key, provider_url, _ = _extract_api_headers(http_request)

    def provider(context):
        return model_candidate_proposal_provider(
            context,
            api_key=api_key,
            provider_url=provider_url,
            model=route.model_id,
            thinking_type=route.thinking_type,
            reasoning_effort=route.reasoning_effort,
        )

    try:
        return await asyncio.to_thread(
            CandidateProposalService().generate,
            run_dir,
            session_id=session_id,
            idempotency_key=request.idempotency_key,
            provider=provider,
            model_profile=route.model_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TimeoutError, ValueError) as exc:
        raise HTTPException(status_code=409, detail={"code": "candidate_proposal_generation_invalid", "message": str(exc)}) from exc


@router.post(
    "/{run_id}/sessions/{session_id}/candidate-proposals/{proposal_id}/approve",
    response_model=CandidateProposalResult,
)
async def approve_candidate_proposal(
    run_id: str,
    session_id: str,
    proposal_id: str,
    request: CandidateProposalDecisionRequest,
):
    run_dir = _run_dir(run_id)
    try:
        return await asyncio.to_thread(
            CandidateProposalService().approve,
            run_dir,
            session_id=session_id,
            proposal_id=proposal_id,
            approved_by=request.approved_by,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "candidate_proposal_approval_invalid", "message": str(exc)}) from exc


@router.post(
    "/{run_id}/sessions/{session_id}/candidate-proposals/{proposal_id}/reject",
    response_model=CandidateProposalResult,
)
async def reject_candidate_proposal(
    run_id: str,
    session_id: str,
    proposal_id: str,
    request: CandidateProposalDecisionRequest,
):
    run_dir = _run_dir(run_id)
    try:
        return await asyncio.to_thread(
            CandidateProposalService().reject,
            run_dir,
            session_id=session_id,
            proposal_id=proposal_id,
            rejected_by=request.approved_by,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "candidate_proposal_rejection_invalid", "message": str(exc)}) from exc


@router.post("/{run_id}/promotions", response_model=PromotionResult)
async def promote_candidate(run_id: str, request: PromotionInput):
    run_dir = _run_dir(run_id)
    try:
        return PromotionControlService().promote(run_dir, value=request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "promotion_invalid", "message": str(exc)}) from exc


def _run_dir(run_id: str):
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="run not found")
    return run_dir
