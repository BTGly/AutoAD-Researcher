"""Reply planner for V2 — LLM-first, answerability-driven fallback."""

from __future__ import annotations

import json
import re
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
    turn_gate = llm_context.get("turn_gate_decision", {}) or {}

    if turn_gate.get("contract_action") in {"answer_without_contract_update", "ask_clarifying_question"}:
        if api_key:
            return _llm_reply(llm_context, user_input, api_key, provider_url)
        return "answer", _non_contract_fallback(turn_gate)

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
    turn_gate = llm_context.get("turn_gate_decision", {})
    blocking = llm_context.get("answerability", {}).get("blocking_next_step", "")
    evidence_text = "\n---\n".join(readable[:3]) if readable else "无可用 evidence"
    confirmed_text = "\n".join(f"{k}: {v}" for k, v in confirmed.items()) if confirmed else "无"
    contract_text = _json_text(contract) if contract else "{}"
    turn_gate_text = _json_text(turn_gate) if turn_gate else "{}"

    system = (
        "你是 AutoAD Researcher v2，科研资料对齐助手。\n"
        "你的内部任务是把用户的实验目标整理成一份 ResearchIntentContract，\n"
        "但用户不是来填合同的——合同只是后台状态，不要反复打断用户。\n"
        "\n"
        "行为准则：\n"
        "1. 用户说“你是谁/你好/我是谁/开玩笑/普通闲聊”时：\n"
        "   自然回答，不要追问 baseline、dataset、metric。不要写入合同 draft。\n"
        "2. 用户问“你是谁”时：回答你是 AutoAD Researcher，负责协助异常检测/深度学习研究任务的资料对齐、目标澄清和方案规划。\n"
        "3. 用户问“我是谁”时：回答只能看到当前任务中提供的研究信息，不能知道真实身份。\n"
        "4. 用户粘贴 GitHub/arXiv/URL 时：简洁登记，不要复述整份合同，不要问“是否确认”。\n"
        "5. 用户提供 baseline/dataset/metric/success criteria 时：可以更新后台 draft，但回复要自然。一轮最多问一个真正阻塞的问题。\n"
        "6. 用户没有提供 improvement_idea 或 target_module 时：不要追问。这些是后续 experiment agents 的工作。\n"
        "7. 如果合同信息基本足够：可以简短总结并问是否确认。不要每轮展示完整字段清单。\n"
        "8. 上一轮你明确请求确认 + 用户回复“确认/可以/没问题/就这样/同意”：视为确认。\n"
        "9. missing fields/ready_for_plan 是后台推理状态，不要默认展示给用户。除非用户主动问“当前合同是什么”。\n"
        "10. 用户粘贴资料后，优先告诉用户后台正在处理什么、右侧 Evidence 会出现什么摘要。\n"
        "\n"
        "参考规则（抄自成熟产品）：\n"
        "- 除非用户主动问“当前合同是什么”，不要告诉用户你在更新合同草稿，直接更新就好。（抄自 Cursor todo_write）\n"
        "- 有疑问时，优先自然对话和回答问题，不要进入合同收集模式。只有用户明确在推进研究任务时才收集字段。（抄自 Claude Code EnterPlanMode）\n"
        "- 如果缺某个字段确实阻塞了下一步，直接问用户。不要绕弯子，也别因为怕“太像填表”而不敢问。（抄自 Devin）\n"
        "\n"
        "输出格式：\n"
        "reply_to_user: string — 用户可见的回复\n"
        "contract_updates: object — 只有涉及研究时才非空\n"
        "missing_required_fields: array — 后台状态\n"
        "next_question: string — 只有确实需要追问时才填写\n"
        "ready_for_confirmation: boolean\n"
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": f"当前状态: {blocking or 'idle'}"},
        {"role": "system", "content": f"已确认事实:\n{confirmed_text}"},
        {"role": "system", "content": f"TurnGateDecision:\n{turn_gate_text}"},
        {"role": "system", "content": f"ResearchIntentContract draft:\n{contract_text}"},
        {"role": "system", "content": f"可用 evidence:\n{evidence_text}"},
        {"role": "user", "content": user_input},
    ]

    from autoad_researcher.ui.chat_client import call_research_chat
    result = call_research_chat(api_key, provider_url, messages, model="deepseek-v4-flash", timeout_s=30)

    if result.get("reply") and not result.get("error"):
        payload = _parse_llm_contract_reply(str(result["reply"]))
        if payload is not None:
            return "answer", _visible_reply_from_llm_payload(payload)
        return "answer", str(result["reply"])

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


def _non_contract_fallback(turn_gate: dict[str, Any] | None = None) -> str:
    instruction = _clean_visible_text((turn_gate or {}).get("next_reply_instruction"))
    if instruction:
        return instruction
    return "这句话不会写入研究合同。需要继续推进研究任务时，可以直接告诉我实验目标、数据集、指标、资料链接或仓库。"


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return "{}"


def _parse_llm_contract_reply(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _visible_reply_from_llm_payload(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    reply = _clean_visible_text(payload.get("reply_to_user"))
    question = _clean_visible_text(payload.get("next_question"))
    if reply:
        parts.append(reply)
    if question and question != reply:
        parts.append(question)
    if not parts:
        parts.append("我已更新研究意图草稿。请继续补充目标、指标或成功标准。")
    return "\n\n".join(parts)


def _clean_visible_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
