import asyncio
import json
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


def _extract_api_headers(request: Request) -> tuple[str, str, str]:
    """Read API config from request headers first, then fallback to server env."""
    api_key = request.headers.get("X-AutoAD-API-Key", "")
    provider = request.headers.get("X-AutoAD-Base-URL", "")
    model = request.headers.get("X-AutoAD-Model", "")

    if not api_key:
        from autoad_researcher.ui.v2_config import get_api_key
        api_key = get_api_key()
    if not provider:
        from autoad_researcher.ui.v2_config import get_provider_url
        provider = get_provider_url()
    if not model:
        model = "deepseek-v4-flash"

    return api_key, provider, model


@router.post("/send", response_model=ChatResponse)
async def chat_send(req: ChatRequest, request: Request):
    run_dir = Path("runs") / req.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    api_key, provider_url, _ = _extract_api_headers(request)
    stored_transcript_tail = _load_transcript_tail(run_dir)
    transcript_tail = req.transcript_tail or stored_transcript_tail

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=req.user_input,
        attachments=req.attachments or None,
        transcript_tail=transcript_tail,
        api_key=api_key,
        provider_url=provider_url,
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

    message_id = f"assistant_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    for chunk in _reply_chunks(result.reply):
        await manager.broadcast(req.run_id, {
            "type": "assistant.delta",
            "message_id": message_id,
            "content": chunk,
        })
        await asyncio.sleep(0)
    await manager.broadcast(req.run_id, {
        "type": "assistant.done",
        "message_id": message_id,
        "reply_kind": result.reply_kind,
    })

    return ChatResponse(
        reply=result.reply,
        reply_kind=result.reply_kind,
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


def _reply_chunks(text: str, chunk_size: int = 96) -> list[str]:
    if not text:
        return [""]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size])
        start += chunk_size
    return chunks
