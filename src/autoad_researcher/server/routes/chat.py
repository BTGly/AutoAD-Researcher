from pathlib import Path

from fastapi import APIRouter

from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.server.models import ChatRequest, ChatResponse
from autoad_researcher.server.ws_manager import manager
from autoad_researcher.ui.v2_config import get_api_key, get_provider_url

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/send", response_model=ChatResponse)
async def chat_send(req: ChatRequest):
    run_dir = Path("runs") / req.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    api_key = get_api_key()
    provider_url = get_provider_url()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=req.user_input,
        attachments=req.attachments or None,
        api_key=api_key,
        provider_url=provider_url,
    )

    # Broadcast events via WebSocket for real-time frontend updates
    if result.created_sources:
        for src in result.created_sources:
            await manager.broadcast(req.run_id, {
                "type": "source.created",
                "source_id": src.get("source_id", ""),
                "kind": src.get("kind", ""),
            })

    await manager.broadcast(req.run_id, {
        "type": "assistant.done",
        "reply_kind": result.reply_kind,
    })
    await manager.broadcast(req.run_id, {
        "type": "assistant.delta",
        "content": result.reply,
    })

    return ChatResponse(
        reply=result.reply,
        reply_kind=result.reply_kind,
    )
