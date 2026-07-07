"""Reply planner for V2 — LLM-first, answerability-driven fallback."""

from __future__ import annotations

from typing import Any


def plan_reply(
    llm_context: dict[str, Any],
    user_input: str,
    *,
    api_key: str = "",
    provider_url: str = "",
) -> tuple[str, str]:
    """Return (reply_kind, reply_text).

    All user input goes through LLM when api_key is available.
    Fallback is evidence-state structured — no keyword matching, no fixed templates.
    """

    answerability = llm_context.get("answerability", {})
    blocking = answerability.get("blocking_next_step", "")
    usable = llm_context.get("usable_evidence", [])
    unparsed = llm_context.get("unparsed_sources", [])
    readable = llm_context.get("readable_summaries", [])

    if api_key:
        return _llm_reply(llm_context, user_input, api_key, provider_url)

    return _unified_fallback(blocking, len(unparsed), len(usable), len(readable))


def _llm_reply(
    llm_context: dict[str, Any],
    user_input: str,
    api_key: str,
    provider_url: str,
) -> tuple[str, str]:
    readable = llm_context.get("readable_summaries", [])
    confirmed = llm_context.get("confirmed_from_user", {})
    blocking = llm_context.get("answerability", {}).get("blocking_next_step", "")
    evidence_text = "\n---\n".join(readable[:3]) if readable else "无可用 evidence"
    confirmed_text = "\n".join(f"{k}: {v}" for k, v in confirmed.items()) if confirmed else "无"

    system = (
        "你是 AutoAD Researcher v2，科研资料对齐助手。\n"
        "基于 evidence 回答用户。回复简洁，不超过 5 行。\n"
        "不要编造证据中没有的内容。evidence 不足时直接说缺什么。\n"
        "用户问'你是谁'、'能做什么'——基于本 prompt 自然回答。"
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": f"当前状态: {blocking or 'idle'}"},
        {"role": "system", "content": f"已确认事实:\n{confirmed_text}"},
        {"role": "system", "content": f"可用 evidence:\n{evidence_text}"},
        {"role": "user", "content": user_input},
    ]

    from autoad_researcher.ui.chat_client import call_research_chat
    result = call_research_chat(api_key, provider_url, messages, model="deepseek-v4-flash", timeout_s=30)

    if result.get("reply") and not result.get("error"):
        return "answer", result["reply"]

    return _unified_fallback(blocking, 0, 0, 0)


def _unified_fallback(blocking: str, unparsed_count: int, usable_count: int, readable_count: int) -> tuple[str, str]:
    """Evidence-state fallback — no keyword templates."""
    parts = [f"当前状态: {blocking or 'idle'}"]

    if unparsed_count:
        parts.append(f"未解析 source: {unparsed_count}")
    if usable_count:
        parts.append(f"可用 evidence: {usable_count}")
    if readable_count:
        parts.append(f"可读摘要: {readable_count}")

    parts.append("配置 API Key 后可调用 DeepSeek 生成自然回复。")

    return "answer", "\n".join(parts)
