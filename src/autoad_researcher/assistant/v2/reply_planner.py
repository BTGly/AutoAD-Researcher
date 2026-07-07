"""Reply planner for V2. answerability-driven, with optional LLM for natural replies."""

from __future__ import annotations

from typing import Any


def plan_reply(
    llm_context: dict[str, Any],
    user_input: str,
    *,
    api_key: str = "",
    provider_url: str = "",
) -> tuple[str, str]:
    answerability = llm_context.get("answerability", {})
    can_answer = answerability.get("can_answer", False)
    blocking = answerability.get("blocking_next_step", "")
    usable = llm_context.get("usable_evidence", [])
    candidates = llm_context.get("candidate_sources", [])
    unparsed = llm_context.get("unparsed_sources", [])
    confirmed = llm_context.get("confirmed_from_user", {})
    readable = llm_context.get("readable_summaries", [])

    # Simple identity / capability questions — no evidence needed
    simple_greetings = ("你是谁", "你能做什么", "hello", "hi", "你好", "帮助", "help")
    if any(w in user_input.lower() for w in simple_greetings):
        return "answer", (
            "我是 AutoAD Researcher v2，科研资料对齐助手。\n\n"
            "我可以帮你：\n"
            "- 上传 PDF → 自动解析 → 讨论论文内容\n"
            "- 粘贴 arXiv/GitHub 链接触发下载和分析\n"
            "- 搜索候选方法\n"
            "- 整理 ResearchContextDraft 给后续实验 agents\n\n"
            "试试点击右上角 🔔 演示 看完整流程。"
        )

    if can_answer and readable:
        if api_key:
            return _llm_reply(llm_context, user_input, api_key, provider_url)
        return _rule_based_answer(readable, user_input)

    if blocking == "parse" and unparsed:
        return "need_parse", (
            f"当前有 {len(unparsed)} 个 PDF 已登记但尚未解析。\n"
            "如已上传 PDF，后台会自动解析；完成后右上角弹出通知。"
        )

    if blocking == "fetch" and candidates:
        return "candidate_only", (
            f"当前有 {len(candidates)} 个候选来源（candidate_source_only）。\n"
            "这些来源尚未 fetch/parse，不能作为 supported facts。"
        )

    if blocking == "intake":
        return "need_acquire", (
            "当前没有已登记的资料。\n\n"
            "你可以：\n"
            "- 上传 PDF 论文（点击右下角 +）\n"
            "- 粘贴 arXiv / GitHub 链接到对话框\n"
            "- 搜索最新论文"
        )

    if confirmed:
        parts = [f"当前已确认：{confirmed.get('dataset', '未指定')} / {confirmed.get('baseline', '未指定')}"]
        return "answer", "\n".join(parts)

    return "answer", "收到。上传 PDF 或粘贴链接开始分析。"


def _llm_reply(
    llm_context: dict[str, Any],
    user_input: str,
    api_key: str,
    provider_url: str,
) -> tuple[str, str]:
    readable = llm_context.get("readable_summaries", [])
    confirmed = llm_context.get("confirmed_from_user", {})
    evidence_text = "\n---\n".join(readable[:3])
    confirmed_text = "\n".join(f"{k}: {v}" for k, v in confirmed.items()) if confirmed else "无"

    system = (
        "你是科研资料对齐助手。基于已有 evidence 回答用户问题。\n"
        "回复不超过 5 行。不要编造证据中没有的内容。\n"
        "如果 evidence 不足，直接说缺什么，不要假装知道。"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": f"当前已确认事实:\n{confirmed_text}"},
        {"role": "system", "content": f"当前可用 evidence:\n{evidence_text}"},
        {"role": "user", "content": user_input},
    ]

    from autoad_researcher.ui.chat_client import call_research_chat
    result = call_research_chat(api_key, provider_url, messages, model="deepseek-v4-flash", timeout_s=30)

    if result.get("reply") and not result.get("error"):
        return "answer_from_evidence", result["reply"]

    return _rule_based_answer(readable, user_input)


def _rule_based_answer(readable: list[str], user_input: str) -> tuple[str, str]:
    summary_text = "\n\n".join(readable[:3])
    return "answer_from_evidence", (
        f"基于已解析资料：\n\n{summary_text}\n\n"
        "你可以继续问具体方法、可迁移性、或研究方案。\n"
        "(当前未配置 API Key，回复为 rule-based。配置后调用 DeepSeek 做自然回答。)"
    )
