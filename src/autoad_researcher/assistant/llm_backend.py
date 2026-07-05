"""Schema-bound LLM backend foundation for AutoAD Assistant.

Round 5 introduces the validation boundary only. It does not call a concrete
provider, read API keys, write confirmed task artifacts, or approve execution.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.draft_schema import ResearchTaskDraftV1
from autoad_researcher.assistant.probe import WhatWeKnow
from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.session import AssistantMode, AutoADAssistantSession


class SchemaBoundOutputError(ValueError):
    """Raised when a backend returns non-object or invalid JSON output."""


class AssistantTextReplyV1(BaseModel):
    """Small schema-bound assistant reply for non-draft modes."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, frozen=True)
    message: str = Field(min_length=1)
    proposed_task_summary: str | None = None
    blocking_questions: list[str] = Field(default_factory=list, max_length=2)
    warnings: list[str] = Field(default_factory=list)


class SchemaBoundLLMRequest(BaseModel):
    """Provider-neutral request passed to an injected LLM client."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    mode: AssistantMode
    prompt_id: str
    system_prompt: str
    output_schema: str
    context: dict[str, Any] = Field(default_factory=dict)


class SchemaBoundLLMResult(BaseModel):
    """Validated schema-bound backend result."""

    model_config = ConfigDict(extra="forbid")

    request: SchemaBoundLLMRequest
    parsed_output: dict[str, Any]


class SchemaJSONClient(Protocol):
    """Minimal client protocol for future LLM adapters."""

    def complete_json(self, request: SchemaBoundLLMRequest) -> dict[str, Any] | str:
        ...


T = TypeVar("T", bound=BaseModel)


class StaticSchemaJSONClient:
    """Deterministic client for tests and offline development."""

    def __init__(self, payload: dict[str, Any] | str) -> None:
        self.payload = payload
        self.requests: list[SchemaBoundLLMRequest] = []

    def complete_json(self, request: SchemaBoundLLMRequest) -> dict[str, Any] | str:
        self.requests.append(request)
        return self.payload


class SchemaBoundAssistantBackend:
    """Validate injected LLM JSON output against concrete Pydantic schemas."""

    def __init__(self, client: SchemaJSONClient, *, selector: PromptSelector | None = None) -> None:
        self._client = client
        self._selector = selector or PromptSelector()

    def complete_text_reply(
        self,
        *,
        session: AutoADAssistantSession,
        what_we_know: WhatWeKnow,
        mode: AssistantMode | None = None,
    ) -> AssistantTextReplyV1:
        selected_mode = mode or session.mode
        request = self._request(
            session=session,
            what_we_know=what_we_know,
            mode=selected_mode,
            output_schema="AssistantTextReplyV1",
            system_prompt=self._selector.build_system_prompt_for_mode(selected_mode),
            prompt_id=self._selector.prompt_id_for_mode(selected_mode),
        )
        return self._complete_model(request, AssistantTextReplyV1)

    def complete_research_task_draft(
        self,
        *,
        session: AutoADAssistantSession,
        what_we_know: WhatWeKnow,
    ) -> ResearchTaskDraftV1:
        request = self._request(
            session=session,
            what_we_know=what_we_know,
            mode="intent_structuring",
            output_schema="ResearchTaskDraftV1",
            system_prompt=self._selector.build_research_task_draft_prompt(),
            prompt_id="assistant.research_task_draft.v1",
        )
        return self._complete_model(request, ResearchTaskDraftV1)

    def complete_with_result(
        self,
        *,
        session: AutoADAssistantSession,
        what_we_know: WhatWeKnow,
        output_model: type[T],
        mode: AssistantMode | None = None,
    ) -> tuple[T, SchemaBoundLLMResult]:
        selected_mode = mode or session.mode
        request = self._request(
            session=session,
            what_we_know=what_we_know,
            mode=selected_mode,
            output_schema=output_model.__name__,
            system_prompt=self._selector.build_system_prompt_for_mode(selected_mode),
            prompt_id=self._selector.prompt_id_for_mode(selected_mode),
        )
        payload = self._payload_as_dict(self._client.complete_json(request))
        parsed = output_model.model_validate(payload)
        return parsed, SchemaBoundLLMResult(request=request, parsed_output=parsed.model_dump(mode="json"))

    def _complete_model(self, request: SchemaBoundLLMRequest, output_model: type[T]) -> T:
        payload = self._payload_as_dict(self._client.complete_json(request))
        return output_model.model_validate(payload)

    @staticmethod
    def _payload_as_dict(payload: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise SchemaBoundOutputError("LLM output must be JSON object text") from exc
        if not isinstance(payload, dict):
            raise SchemaBoundOutputError("LLM output must be a JSON object")
        return payload

    @staticmethod
    def _request(
        *,
        session: AutoADAssistantSession,
        what_we_know: WhatWeKnow,
        mode: AssistantMode,
        output_schema: str,
        system_prompt: str,
        prompt_id: str,
    ) -> SchemaBoundLLMRequest:
        return SchemaBoundLLMRequest(
            run_id=session.run_id,
            mode=mode,
            prompt_id=prompt_id,
            system_prompt=system_prompt,
            output_schema=output_schema,
            context={
                "session": session.model_dump(mode="json"),
                "what_we_know": what_we_know.model_dump(mode="json"),
            },
        )
