from pathlib import Path

from fastapi import APIRouter

from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.server.models import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/send", response_model=ChatResponse)
async def chat_send(req: ChatRequest):
    run_dir = Path("runs") / req.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=req.user_input,
        attachments=req.attachments or None,
    )

    return ChatResponse(
        reply=result.reply,
        reply_kind=result.reply_kind,
    )
