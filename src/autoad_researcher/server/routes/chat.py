import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.server.models import ChatRequest, ChatResponse
from autoad_researcher.assistant.v2.event_service import append_event
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
        model = _load_config_value("model") or DEFAULT_MODEL

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


def _extract_experiment_headers(request: Request) -> dict[str, str]:
    """Read experiment agent config from request headers."""
    return {
        "provider": request.headers.get("X-AutoAD-Exp-Provider", ""),
        "model": request.headers.get("X-AutoAD-Exp-Model", ""),
        "api_key": request.headers.get("X-AutoAD-Exp-Api-Key", ""),
        "base_url": request.headers.get("X-AutoAD-Exp-Base-URL", ""),
        "reasoning_effort": request.headers.get("X-AutoAD-Exp-Reasoning", ""),
        "max_cycles": request.headers.get("X-AutoAD-Exp-Max-Cycles", ""),
        "max_turns": request.headers.get("X-AutoAD-Exp-Max-Turns", ""),
        "executor_timeout": request.headers.get("X-AutoAD-Exp-Timeout", ""),
        "search_enabled": request.headers.get("X-AutoAD-Exp-Search", "0"),
        "auto_search": request.headers.get("X-AutoAD-Exp-Auto-Search", "0"),
    }


@router.post("/send", response_model=ChatResponse)
async def chat_send(req: ChatRequest, request: Request):
    run_dir = Path("runs") / req.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    api_key, provider_url, _ = _extract_api_headers(request)
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
