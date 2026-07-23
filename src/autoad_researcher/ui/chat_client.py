"""Compatibility facade for the shared Research Assistant LLM runtime."""

from collections.abc import Callable
from typing import Any, Literal

from autoad_researcher.assistant.llm_runtime import (
    CallPriority,
    LLMCallRequest,
    get_llm_call_broker,
)


def call_research_chat(
    api_key: str,
    provider_base_url: str,
    messages: list[dict[str, Any]],
    model: str = "deepseek-v4-flash",
    timeout_s: int | float = 60,
    on_delta: Callable[[str], None] | None = None,
    *,
    priority: CallPriority = "contract",
    response_format_json: bool = False,
    max_tokens: int | None = None,
    temperature: float | None = None,
    thinking_type: Literal["enabled", "disabled"] | None = None,
    reasoning_effort: Literal["high", "max"] | None = None,
    tools: list[dict[str, Any]] | None = None,
    on_reasoning_delta: Callable[[str], None] | None = None,
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
        tools=tools,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking_type=thinking_type,
        reasoning_effort=reasoning_effort,
        on_delta=on_delta,
        on_reasoning_delta=on_reasoning_delta,
    ))
    return result.as_public_dict()
