"""LLM-first turn gate for HF-2 contract updates."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Literal

from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.v2.llm_trace_service import append_llm_trace
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
    run_dir: Path | None = None,
) -> TurnGateDecision:
    """Decide turn routing through an LLM gate.

    Natural-language relevance is never decided by keyword rules here. Without
    a model, natural language is treated conservatively as ordinary chat. Source
    intake/job events are structured system events and may enter the pipeline.
    """

    if (created_sources or created_jobs) and not api_key:
        return TurnGateDecision(
            turn_type="source_intake",
            contract_action="answer_without_contract_update",
            contract_update_allowed=False,
            need_discovery_allowed=False,
            save_draft_allowed=False,
            user_intent_summary="structured source/job intake",
            evidence_from_current_turn=["created_sources_or_jobs"],
            confidence=1.0,
            reason="Source/job events register materials; offline fallback does not infer research contract fields from source intake.",
            next_reply_instruction="资料已登记并进入后台处理；不更新研究合同草案。",
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
        created_sources=created_sources,
        created_jobs=created_jobs,
        answerability=answerability,
    )
    selector = PromptSelector()
    profile = selector.profile_for_v2_component("turn_gate")
    system_prompt = messages[0]["content"] if messages else ""
    model = "deepseek-v4-flash"

    from autoad_researcher.ui.chat_client import call_research_chat

    started = time.perf_counter()
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model,
        timeout_s=30,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    reply_text = str(result.get("reply") or "")
    payload = _parse_json_object(reply_text)
    if result.get("error") or payload is None:
        append_llm_trace(
            run_dir,
            call_site="turn_gate",
            prompt_id=profile.prompt_id,
            prompt_version=profile.prompt_version,
            prompt_text=system_prompt,
            model=model,
            provider_url=provider_url,
            messages=messages,
            raw_output=reply_text,
            parse_status="error",
            schema_validation="skipped",
            fallback_reason="llm_error_or_non_json",
            latency_ms=latency_ms,
        )
        return _offline_no_contract_decision(
            user_input=user_input,
            transcript_tail=transcript_tail,
            existing_contract_draft=existing_contract_draft,
        )
    try:
        decision = TurnGateDecision.model_validate(payload)
    except Exception:
        append_llm_trace(
            run_dir,
            call_site="turn_gate",
            prompt_id=profile.prompt_id,
            prompt_version=profile.prompt_version,
            prompt_text=system_prompt,
            model=model,
            provider_url=provider_url,
            messages=messages,
            raw_output=reply_text,
            parse_status="ok",
            schema_validation="error",
            fallback_reason="schema_validation_error",
            latency_ms=latency_ms,
        )
        return _offline_no_contract_decision(
            user_input=user_input,
            transcript_tail=transcript_tail,
            existing_contract_draft=existing_contract_draft,
        )
    append_llm_trace(
        run_dir,
        call_site="turn_gate",
        prompt_id=profile.prompt_id,
        prompt_version=profile.prompt_version,
        prompt_text=system_prompt,
        model=model,
        provider_url=provider_url,
        messages=messages,
        raw_output=reply_text,
        parse_status="ok",
        schema_validation="ok",
        latency_ms=latency_ms,
    )
    return _validate_turn_gate_decision(decision)


def _build_turn_gate_messages(
    *,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
    existing_contract_draft: dict[str, Any] | None,
    created_sources: list[dict[str, Any]] | None = None,
    created_jobs: list[dict[str, Any]] | None = None,
    answerability: dict[str, Any],
) -> list[dict[str, str]]:
    system = PromptSelector().build_system_prompt_for_v2_component("turn_gate")
    context = {
        "transcript_tail": transcript_tail or [],
        "existing_contract_draft": existing_contract_draft or {},
        "created_sources": created_sources or [],
        "created_jobs": created_jobs or [],
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
