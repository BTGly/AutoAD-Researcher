"""Compatibility facade for the shared Research Assistant LLM runtime."""

from collections.abc import Callable
from typing import Any

from autoad_researcher.assistant.llm_runtime import (
    CallPriority,
    LLMCallRequest,
    get_llm_call_broker,
)


def call_research_chat(
    api_key: str,
    provider_base_url: str,
    messages: list[dict[str, str]],
    model: str = "deepseek-chat",
    timeout_s: int | float = 60,
    on_delta: Callable[[str], None] | None = None,
    *,
    priority: CallPriority = "contract",
    response_format_json: bool = False,
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Call the shared broker and return a safe ``{reply, error, runtime}`` mapping."""

    result = get_llm_call_broker().call(LLMCallRequest(
        api_key=api_key,
        provider_base_url=provider_base_url,
        messages=messages,
        model=model,
        timeout_s=float(timeout_s),
        priority=priority,
        response_format_json=response_format_json,
        max_tokens=max_tokens,
        temperature=temperature,
        on_delta=on_delta,
    ))
    return result.as_public_dict()
