from pathlib import Path

from fastapi import APIRouter, Request

from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.server.models import ChatRequest, ChatResponse
from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.server.ws_manager import manager

router = APIRouter(prefix="/api/chat", tags=["chat"])


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

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=req.user_input,
        attachments=req.attachments or None,
        api_key=api_key,
        provider_url=provider_url,
    )

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
        "type": "assistant.delta",
        "content": result.reply,
    })
    await manager.broadcast(req.run_id, {
        "type": "assistant.done",
        "reply_kind": result.reply_kind,
    })

    return ChatResponse(
        reply=result.reply,
        reply_kind=result.reply_kind,
    )
