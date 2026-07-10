from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_input: str
    run_id: str = "run_default"
    request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    attachments: list[str] = Field(default_factory=list)
    transcript_tail: list[dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    reply_kind: str = "answer"


class RunInfo(BaseModel):
    run_id: str
    created_at: str | None = None
    sources_count: int = 0
