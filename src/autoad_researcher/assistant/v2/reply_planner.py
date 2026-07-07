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
    contract = llm_context.get("research_intent_contract", {})
    blocking = llm_context.get("answerability", {}).get("blocking_next_step", "")
    evidence_text = "\n---\n".join(readable[:3]) if readable else "无可用 evidence"
    confirmed_text = "\n".join(f"{k}: {v}" for k, v in confirmed.items()) if confirmed else "无"
    contract_text = _json_text(contract) if contract else "{}"

    system = (
        "你是 AutoAD Researcher v2，HF-2 研究意图与实验目标合同助手。\n"
        "你的任务不是让用户设计方法，也不是让用户决定改哪个模块；你的任务是澄清研究目标、baseline、dataset、metric、成功标准、执行模式和防作弊边界。\n"
        "improvement_idea、target_module 只能作为 optional hints；用户没有也不能阻塞。\n"
        "后续 experiment agents 才负责发散候选方向、定位模块、patch、运行实验和分析结果。\n"
        "不要问'你想怎么改'、'你要改哪个模块'、'准备用什么方法'。\n"
        "优先问：主要优化指标/速度/显存/训练成本/复现/稳定性中的哪一个；评价协议是否保持 baseline 原始设置；是否先 plan_only。\n"
        "不要编造 evidence 中没有的内容。每轮必须输出 JSON object，不要输出 Markdown。\n"
        "JSON schema: {"
        "\"reply_to_user\": string, "
        "\"contract_updates\": object, "
        "\"new_user_confirmed_fields\": array, "
        "\"missing_required_fields\": array, "
        "\"optional_hints_detected\": object, "
        "\"next_question\": string, "
        "\"ready_for_confirmation\": boolean, "
        "\"ready_for_experiment_agents\": boolean"
        "}."
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": f"当前状态: {blocking or 'idle'}"},
        {"role": "system", "content": f"已确认事实:\n{confirmed_text}"},
        {"role": "system", "content": f"ResearchIntentContract draft:\n{contract_text}"},
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

    parts.append("HF-2 当前只需要确认研究意图，不需要你先设计具体方法或指定要改哪个模块。")
    parts.append("你有没有已有改进想法？没有也可以，后续 experiment agents 会自动探索。")
    parts.append("请先确认主要目标：指标效果、推理速度、显存占用、训练成本、复现跑通，还是稳定性/泛化？")
    parts.append("默认禁止修改测试集、指标定义、数据划分、测试标签和任何标签泄漏；当前执行模式默认 plan_only。")

    return "answer", "\n".join(parts)


def _json_text(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return "{}"
