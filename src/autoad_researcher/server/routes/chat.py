import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from autoad_researcher.assistant.model_routing import ModelRole, ModelRoute, select_model_route
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.server.models import ChatRequest, ChatResponse
from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.conversation_store import (
    TRANSCRIPT_RELATIVE_PATH,
    append_message,
    load_message_tail_for_llm,
)
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400
from autoad_researcher.server.ws_manager import manager

router = APIRouter(prefix="/api/chat", tags=["chat"])

CONFIG_PATH = Path.home() / ".autoad" / "config.json"
DEFAULT_PROVIDER = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


def _extract_api_headers(request: Request) -> tuple[str, str, str]:
    """Read API config from request headers first, then fallback to server env."""
    api_key = request.headers.get("X-AutoAD-API-Key", "")
    provider = request.headers.get("X-AutoAD-Base-URL", "")
    model = request.headers.get("X-AutoAD-Model", "")

    if not api_key:
        api_key = _load_config_value("api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
    if not provider:
        provider = _load_config_value("provider_url") or DEFAULT_PROVIDER
    if not model:
        model = _load_config_value("dialogue_model") or _load_config_value("model") or DEFAULT_MODEL

    return api_key, provider, model


def _load_config_value(key: str) -> str:
    if not CONFIG_PATH.is_file():
        return ""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    value = cfg.get(key, "")
    return value if isinstance(value, str) else ""


def _extract_role_route(request: Request, role: ModelRole) -> ModelRoute:
    """Resolve a role model while keeping credentials outside durable state."""
    _api_key, _provider, dialogue_fallback = _extract_api_headers(request)
    header_name = {
        "research_dialogue": "X-AutoAD-Dialogue-Model",
        "report": "X-AutoAD-Report-Model",
        "experiment_agent": "X-AutoAD-Experiment-Model",
    }[role]
    requested = request.headers.get(header_name, "")
    if not requested and role == "research_dialogue":
        requested = dialogue_fallback
    if not requested and role == "report":
        requested = _load_config_value("report_model") or os.environ.get("AUTOAD_REPORT_MODEL", "")
    if not requested and role == "experiment_agent":
        requested = _load_config_value("experiment_model") or os.environ.get("AUTOAD_EXPERIMENT_MODEL", "")
    try:
        return select_model_route(role, requested or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/send", response_model=ChatResponse)
async def chat_send(req: ChatRequest, request: Request):
    run_dir = run_dir_or_400(RUNS_ROOT, req.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    api_key, provider_url, _model = _extract_api_headers(request)
    route = _extract_role_route(request, "research_dialogue")
    append_event(run_dir, "assistant.model_route.selected", route.snapshot())
    stored_transcript_tail = _load_transcript_tail(run_dir)
    # The durable transcript includes worker notifications; browser state is
    # intentionally not authoritative when a background task finishes mid-chat.
    transcript_tail = stored_transcript_tail
    message_id = _resolve_message_id(req.request_id)
    loop = asyncio.get_running_loop()

    def on_reply_delta(delta: str) -> None:
        if not delta:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(req.run_id, {
                    "type": "assistant.delta",
                    "message_id": message_id,
                    "content": delta,
                }),
                loop,
            )
        except RuntimeError:
            return

    result = await asyncio.to_thread(
        ResearchOrchestratorV2.handle,
        run_dir,
        user_input=req.user_input,
        attachments=req.attachments or None,
        transcript_tail=transcript_tail,
        api_key=api_key,
        provider_url=provider_url,
        model=route.model_id,
        model_route=route,
        on_reply_delta=on_reply_delta,
    )
    _append_transcript(run_dir, "user", req.user_input)
    _append_transcript(run_dir, "assistant", result.reply)

    # Broadcast created_sources and created_jobs
    for src in result.created_sources:
        event = append_event(run_dir, "source.created", {
            "source_id": src.get("source_id", ""),
            "kind": src.get("kind", ""),
            "stored_path": src.get("stored_path", ""),
            "status": src.get("status", ""),
        })
        await manager.broadcast(req.run_id, {
            "type": "source.created",
            "event_id": event.get("event_id"),
            "created_at": event.get("created_at"),
            "source_id": src.get("source_id", ""),
            "kind": src.get("kind", ""),
            "stored_path": src.get("stored_path", ""),
        })

    for job in result.created_jobs:
        event = append_event(run_dir, "job.queued", {
            "job_id": job.get("job_id", ""),
            "job_type": job.get("job_type", ""),
            "source_id": job.get("source_id", ""),
        })
        await manager.broadcast(req.run_id, {
            "type": "job.queued",
            "event_id": event.get("event_id"),
            "created_at": event.get("created_at"),
            "job_id": job.get("job_id", ""),
            "job_type": job.get("job_type", ""),
        })

    await manager.broadcast(req.run_id, {
        "type": "assistant.done",
        "message_id": message_id,
        "reply_kind": result.reply_kind,
        "content": result.reply,
    })

    return ChatResponse(
        reply=result.reply,
        reply_kind=result.reply_kind,
        source_action=result.source_action,
        experiment_task=result.experiment_task,
        action_receipts=result.action_receipts,
        material_action_status=result.material_action_status,
    )


def _load_transcript_tail(run_dir: Path, limit: int = 12) -> list[dict[str, Any]]:
    return load_message_tail_for_llm(run_dir, limit=limit)


def _append_transcript(run_dir: Path, role: str, content: str) -> None:
    append_message(run_dir, role=role, content=content)


def _resolve_message_id(request_id: str | None) -> str:
    if request_id:
        return request_id
    from datetime import datetime, timezone
    return f"assistant_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
