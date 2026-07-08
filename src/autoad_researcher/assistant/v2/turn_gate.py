"""LLM-first turn gate for HF-2 contract updates."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TurnGateDecision(BaseModel):
    """Decision for whether a user turn may enter the contract pipeline."""

    model_config = ConfigDict(extra="forbid")

    turn_type: Literal[
        "contract_update",
        "contract_confirmation",
        "contract_question",
        "source_intake",
        "ordinary_chat",
        "joke",
        "frustration",
        "identity_question",
        "ambiguous",
    ]
    contract_action: Literal[
        "update_contract",
        "confirm_contract",
        "answer_without_contract_update",
        "ask_clarifying_question",
    ]
    contract_update_allowed: bool
    need_discovery_allowed: bool
    save_draft_allowed: bool
    user_intent_summary: str = ""
    evidence_from_current_turn: list[str] = Field(default_factory=list)
    evidence_from_context: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    next_reply_instruction: str | None = None


def decide_turn_gate_with_llm(
    *,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
    existing_contract_draft: dict[str, Any] | None,
    created_sources: list[dict[str, Any]],
    created_jobs: list[dict[str, Any]],
    answerability: dict[str, Any],
    api_key: str,
    provider_url: str,
) -> TurnGateDecision:
    """Decide turn routing through an LLM gate.

    Natural-language relevance is never decided by keyword rules here. Without
    a model, natural language is treated conservatively as ordinary chat. Source
    intake/job events are structured system events and may enter the pipeline.
    """

    if created_sources or created_jobs:
        return TurnGateDecision(
            turn_type="source_intake",
            contract_action="update_contract",
            contract_update_allowed=True,
            need_discovery_allowed=True,
            save_draft_allowed=True,
            user_intent_summary="structured source/job intake",
            evidence_from_current_turn=["created_sources_or_jobs"],
            confidence=1.0,
            reason="Source/job events are structured system state, not natural-language relevance.",
        )

    if not api_key:
        return _offline_no_contract_decision(
            user_input=user_input,
            transcript_tail=transcript_tail,
            existing_contract_draft=existing_contract_draft,
        )

    messages = _build_turn_gate_messages(
        user_input=user_input,
        transcript_tail=transcript_tail,
        existing_contract_draft=existing_contract_draft,
        answerability=answerability,
    )

    from autoad_researcher.ui.chat_client import call_research_chat

    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model="deepseek-v4-flash",
        timeout_s=30,
    )
    payload = _parse_json_object(str(result.get("reply") or ""))
    if result.get("error") or payload is None:
        return _offline_no_contract_decision(
            user_input=user_input,
            transcript_tail=transcript_tail,
            existing_contract_draft=existing_contract_draft,
        )
    try:
        decision = TurnGateDecision.model_validate(payload)
    except Exception:
        return _offline_no_contract_decision(
            user_input=user_input,
            transcript_tail=transcript_tail,
            existing_contract_draft=existing_contract_draft,
        )
    return _validate_turn_gate_decision(decision)


def _build_turn_gate_messages(
    *,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
    existing_contract_draft: dict[str, Any] | None,
    answerability: dict[str, Any],
) -> list[dict[str, str]]:
    system = (
        "你是 AutoAD Researcher 的 HF-2 Turn Gate。你只输出 TurnGateDecision JSON，不输出 Markdown。\n"
        "你的职责是判断当前用户消息是否允许进入 ResearchIntentContract 合同链路；你不能直接修改合同。\n"
        "你不是关键词分类器。不能仅因为出现 PatchCore、MVTec、AUROC、dataset、metric、实验、论文、仓库等词就判定为合同相关。\n"
        "必须根据当前用户消息、最近上下文、已有合同草稿、上一轮助手行为和语用意图判断。\n"
        "只有当用户明确表达研究目标、实验对象、评价指标、成功标准、执行边界、资料来源、确认/修改已有合同，或请求继续推进研究任务时，才允许进入合同链路。\n"
        "身份问题、玩笑、发泄、辱骂、寒暄、情绪表达、与研究合同无关的对话，不允许更新合同。\n"
        "如果消息含义依赖上下文，例如“可以”“继续”“就这个”“按刚刚那个来”，必须结合 transcript_tail 判断上一轮 assistant 是否刚请求合同确认或研究推进。\n"
        "不确定时优先 answer_without_contract_update 或 ask_clarifying_question，不能贸然 save draft。\n"
        "LLM 不能直接确认最终合同；最终确认必须由 orchestrator 根据 existing draft 和明确确认意图执行。\n"
        "Schema: {turn_type, contract_action, contract_update_allowed, need_discovery_allowed, save_draft_allowed, "
        "user_intent_summary, evidence_from_current_turn, evidence_from_context, confidence, reason, next_reply_instruction}."
    )
    context = {
        "transcript_tail": transcript_tail or [],
        "existing_contract_draft": existing_contract_draft or {},
        "answerability": answerability,
    }
    return [
        {"role": "system", "content": system},
        {"role": "system", "content": "Context JSON:\n" + _json_text(context)},
        {"role": "user", "content": user_input},
    ]


def _validate_turn_gate_decision(decision: TurnGateDecision) -> TurnGateDecision:
    if decision.contract_action == "answer_without_contract_update":
        return decision.model_copy(update={
            "contract_update_allowed": False,
            "need_discovery_allowed": False,
            "save_draft_allowed": False,
        })
    if decision.contract_action == "ask_clarifying_question":
        return decision.model_copy(update={
            "contract_update_allowed": False,
            "need_discovery_allowed": False,
            "save_draft_allowed": False,
        })
    if decision.contract_action == "confirm_contract":
        return decision.model_copy(update={
            "contract_update_allowed": False,
            "need_discovery_allowed": False,
            "save_draft_allowed": False,
        })
    return decision


def _offline_no_contract_decision(
    *,
    user_input: str = "",
    transcript_tail: list[dict[str, Any]] | None = None,
    existing_contract_draft: dict[str, Any] | None = None,
) -> TurnGateDecision:
    """Offline fallback with text-confirmation support.

    Even without LLM, allow text confirmation when:
    1. User says a confirmation keyword, AND
    2. The last assistant message requested confirmation, AND
    3. A draft contract exists.
    """

    if _is_contextual_confirmation(user_input, transcript_tail) and existing_contract_draft:
        return TurnGateDecision(
            turn_type="contract_confirmation",
            contract_action="confirm_contract",
            contract_update_allowed=False,
            need_discovery_allowed=False,
            save_draft_allowed=True,
            user_intent_summary="user confirmed contract via text",
            confidence=0.9,
            reason="Offline text confirmation detected: assistant requested confirmation in previous turn.",
            next_reply_instruction="已确认合同。",
        )

    return TurnGateDecision(
        turn_type="ambiguous",
        contract_action="answer_without_contract_update",
        contract_update_allowed=False,
        need_discovery_allowed=False,
        save_draft_allowed=False,
        user_intent_summary="offline natural-language turn",
        confidence=0.0,
        reason="No LLM turn gate result is available.",
        next_reply_instruction="",
    )


_confirm_phrases = ("确认", "可以", "没问题", "同意", "就这样", "按这个来")
_confirm_request_phrases = ("请回复确认", "是否确认", "确认后", "是否按此合同", "请确认", "回复确认")


def _is_contextual_confirmation(
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
) -> bool:
    """Check if user input is a confirmation in the context of a prior assistant request."""
    if not transcript_tail:
        return False
    user_text = user_input.strip()
    if not any(phrase in user_text for phrase in _confirm_phrases):
        return False
    for entry in reversed(transcript_tail):
        if entry.get("role") == "assistant":
            content = str(entry.get("content", ""))
            if any(phrase in content for phrase in _confirm_request_phrases):
                return True
            break
    return False


def _parse_json_object(text: str) -> dict[str, Any] | None:
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


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return "{}"
