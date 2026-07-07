from pydantic import BaseModel


class ChatRequest(BaseModel):
    user_input: str
    run_id: str = "run_default"
    attachments: list[str] = []


class ChatResponse(BaseModel):
    reply: str
    reply_kind: str = "answer"


class RunInfo(BaseModel):
    run_id: str
    created_at: str | None = None
    sources_count: int = 0
