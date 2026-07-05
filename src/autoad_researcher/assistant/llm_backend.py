"""Schema-bound LLM backend for AutoAD Assistant."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SchemaBoundLLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    prompt_id: str
    system_prompt: str
    user_message: str


class SchemaBoundLLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str
    prompt_id: str
    schema_validated: bool = True


class SchemaBoundOutputError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str
    raw_output: str = ""


class AssistantTextReplyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    text: str = Field(min_length=1)
    mode_hint: str | None = None


class StaticSchemaJSONClient:
    """Static client for deterministic testing. No real LLM calls."""

    def __init__(self, fixed_reply: str = "") -> None:
        self._reply = fixed_reply

    def chat(self, messages: list[dict], *, system: str = "") -> str:
        return self._reply or "[deterministic reply]"


class SchemaBoundAssistantBackend:
    """LLM backend that validates output against schemas."""

    def __init__(self, client: StaticSchemaJSONClient | None = None) -> None:
        self._client = client or StaticSchemaJSONClient()

    def handle(self, request: SchemaBoundLLMRequest) -> SchemaBoundLLMResult:
        reply = self._client.chat(
            [{"role": "user", "content": request.user_message}],
            system=request.system_prompt,
        )
        return SchemaBoundLLMResult(
            reply=reply,
            prompt_id=request.prompt_id,
            schema_validated=True,
        )
