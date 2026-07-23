import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from autoad_researcher.assistant.model_routing import ModelRole, ModelRoute, select_model_route
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.server.models import ChatRequest, ChatResponse
from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400
from autoad_researcher.server.ws_manager import manager

router = APIRouter(prefix="/api/chat", tags=["chat"])

TRANSCRIPT_RELATIVE_PATH = Path("chat") / "transcript.jsonl"
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
    transcript_tail = req.transcript_tail or stored_transcript_tail
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
        await manager.broadcast(req.run_id, {
            "type": "source.created",
            "source_id": src.get("source_id", ""),
            "kind": src.get("kind", ""),
        })

    for job in result.created_jobs:
        append_event(run_dir, "job.queued", {
            "job_id": job.get("job_id", ""),
            "job_type": job.get("job_type", ""),
            "source_id": job.get("source_id", ""),
        })
        await manager.broadcast(req.run_id, {
            "type": "job.queued",
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
    )


def _load_transcript_tail(run_dir: Path, limit: int = 12) -> list[dict[str, Any]]:
    path = run_dir / TRANSCRIPT_RELATIVE_PATH
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = payload.get("role")
        content = payload.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            entries.append({"role": role, "content": content})
    return entries


def _append_transcript(run_dir: Path, role: str, content: str) -> None:
    path = run_dir / TRANSCRIPT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "role": role,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _resolve_message_id(request_id: str | None) -> str:
    if request_id:
        return request_id
    return f"assistant_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
