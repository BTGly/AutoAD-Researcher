"""Schema-bound LLM backend for AutoAD Assistant.

This module owns the validation boundary only. It does not call a concrete
provider, read API keys, confirm tasks, approve execution, or mutate assistant
session state.
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
    """Raised when backend output is not a JSON object."""


class SchemaBoundLLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    mode: AssistantMode
    prompt_id: str
    system_prompt: str
    output_schema: str
    context: dict[str, Any] = Field(default_factory=dict)
    user_message: str | None = None


class SchemaBoundLLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: SchemaBoundLLMRequest
    parsed_output: dict[str, Any]


class AssistantTextReplyV1(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, frozen=True)
    message: str = Field(min_length=1)
    proposed_task_summary: str | None = None
    blocking_questions: list[str] = Field(default_factory=list, max_length=2)
    warnings: list[str] = Field(default_factory=list)
    mode_hint: str | None = None


class SchemaJSONClient(Protocol):
    """Minimal provider-neutral JSON client protocol."""

    def complete_json(self, request: SchemaBoundLLMRequest) -> dict[str, Any] | str:
        ...


T = TypeVar("T", bound=BaseModel)


class StaticSchemaJSONClient:
    """Static client for deterministic testing. No real LLM calls."""

    def __init__(self, payload: dict[str, Any] | str = "") -> None:
        self.payload = payload
        self.requests: list[SchemaBoundLLMRequest] = []

    def complete_json(self, request: SchemaBoundLLMRequest) -> dict[str, Any] | str:
        self.requests.append(request)
        return self.payload

    def chat(self, messages: list[dict], *, system: str = "") -> str:
        if isinstance(self.payload, str):
            return self.payload or "[deterministic reply]"
        return json.dumps(self.payload, ensure_ascii=False)


class SchemaBoundAssistantBackend:
    """LLM backend that validates output against schemas."""

    def __init__(self, client: SchemaJSONClient | None = None, *, selector: PromptSelector | None = None) -> None:
        self._client = client or StaticSchemaJSONClient()
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

    def handle(self, request: SchemaBoundLLMRequest) -> SchemaBoundLLMResult:
        """Compatibility helper for callers that already build the request."""
        payload = self._payload_as_dict(self._client.complete_json(request))
        return SchemaBoundLLMResult(request=request, parsed_output=payload)

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
