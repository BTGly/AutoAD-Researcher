"""Unified semantic Router for source actions, contract gating, and task hints."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from autoad_researcher.assistant.llm_runtime import runtime_trace_fields
from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.v2.llm_trace_service import append_llm_trace
from autoad_researcher.assistant.v2.source_action_planner import (
    SourceActionPlan,
    default_tool_capabilities,
    load_repository_hints,
    validate_source_action_plan,
)
from autoad_researcher.assistant.v2.turn_gate import (
    TurnGateDecision,
    _mutation_evidence_matches,
    _requires_mutation_evidence,
    _validate_task_profile_proposal,
    _validate_turn_gate_decision,
    _validate_turn_gate_payload,
)


TaskProfileProposal = Literal[
    "empirical_model_research",
    "systems_optimization",
    "code_diagnosis",
    "general_research",
]


class ConversationRouteDecision(BaseModel):
    """One strict envelope replacing sequential Source Planner and Turn Gate calls."""

    model_config = ConfigDict(extra="forbid")

    turn_gate: TurnGateDecision
    source_action_plan: SourceActionPlan
    task_profile_proposal: TaskProfileProposal
    task_profile_evidence: str | None = None
    suggested_task_title: str | None = None
    suggested_task_summary: str | None = None
    requires_need_discovery_enrichment: bool


def route_conversation_with_llm(
    *,
    run_dir: Path,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
    existing_contract_draft: dict[str, Any] | None,
    source_registry: list[dict[str, Any]],
    pending_jobs: list[dict[str, Any]],
    created_sources: list[dict[str, Any]],
    created_jobs: list[dict[str, Any]],
    answerability: dict[str, Any],
    api_key: str,
    provider_url: str,
    model: str = "deepseek-v4-flash",
    deterministic_source_plan: SourceActionPlan | None = None,
) -> ConversationRouteDecision:
    """Route one natural-language turn with one bounded provider call."""

    if not api_key:
        return conservative_conversation_route(
            source_action_plan=deterministic_source_plan,
            reason="No conversation Router model is available.",
        )
    repository_hints = load_repository_hints(run_dir)
    profile = PromptSelector().profile_for_v2_component("conversation_router")
    messages = _build_conversation_route_messages(
        user_input=user_input,
        transcript_tail=transcript_tail,
        existing_contract_draft=existing_contract_draft,
        source_registry=source_registry,
        pending_jobs=pending_jobs,
        created_sources=created_sources,
        created_jobs=created_jobs,
        answerability=answerability,
        deterministic_source_plan=deterministic_source_plan,
        repository_hints=[hint.model_dump(mode="json") for hint in repository_hints],
    )
    system_prompt = messages[0]["content"]

    from autoad_researcher.ui.chat_client import call_research_chat

    started = time.perf_counter()
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model,
        timeout_s=8,
        priority="routing",
        response_format_json=True,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    reply_text = str(result.get("reply") or "")
    payload = _parse_json_object(reply_text)
    if result.get("error") or payload is None:
        append_llm_trace(
            run_dir,
            call_site="conversation_router",
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
            **runtime_trace_fields(result),
        )
        return conservative_conversation_route(
            source_action_plan=deterministic_source_plan,
            reason="Conversation Router failed safely.",
        )

    decision, validation_errors, recovery_reasons = _validate_route_payload(
        payload,
        user_input=user_input,
        transcript_tail=transcript_tail,
        deterministic_source_plan=deterministic_source_plan,
        repository_hints=repository_hints,
    )
    if decision is None:
        append_llm_trace(
            run_dir,
            call_site="conversation_router",
            prompt_id=profile.prompt_id,
            prompt_version=profile.prompt_version,
            prompt_text=system_prompt,
            model=model,
            provider_url=provider_url,
            messages=messages,
            raw_output=reply_text,
            parse_status="ok",
            schema_validation="error",
            schema_validation_errors=validation_errors,
            fallback_reason="schema_validation_fallback",
            latency_ms=latency_ms,
            **runtime_trace_fields(result),
        )
        return conservative_conversation_route(
            source_action_plan=deterministic_source_plan,
            reason="Conversation Router schema failed safely.",
        )
    append_llm_trace(
        run_dir,
        call_site="conversation_router",
        prompt_id=profile.prompt_id,
        prompt_version=profile.prompt_version,
        prompt_text=system_prompt,
        model=model,
        provider_url=provider_url,
        messages=messages,
        raw_output=reply_text,
        parse_status="ok",
        schema_validation="recovered" if recovery_reasons else "ok",
        schema_validation_errors=validation_errors,
        fallback_reason=",".join(recovery_reasons),
        latency_ms=latency_ms,
        **runtime_trace_fields(result),
    )
    return decision


def conservative_conversation_route(
    *,
    source_action_plan: SourceActionPlan | None = None,
    reason: str,
) -> ConversationRouteDecision:
    return ConversationRouteDecision(
        turn_gate=TurnGateDecision(
            turn_type="ordinary_chat",
            contract_action="answer_without_contract_update",
            contract_update_allowed=False,
            need_discovery_allowed=False,
            save_draft_allowed=False,
            task_profile_proposal="general_research",
            user_intent_summary="conversation requires no contract mutation",
            confidence=0.0,
            reason=reason,
        ),
        source_action_plan=source_action_plan or SourceActionPlan(
            actions=[], confidence=0.0, reason=reason
        ),
        task_profile_proposal="general_research",
        requires_need_discovery_enrichment=False,
    )


def deterministic_source_route(source_action_plan: SourceActionPlan) -> ConversationRouteDecision:
    """Route a structured attachment or URL without any provider call."""

    return ConversationRouteDecision(
        turn_gate=TurnGateDecision(
            turn_type="source_intake",
            contract_action="answer_without_contract_update",
            contract_update_allowed=False,
            need_discovery_allowed=False,
            save_draft_allowed=False,
            task_profile_proposal="general_research",
            user_intent_summary="structured source intake",
            confidence=1.0,
            reason="Structured source input is handled deterministically.",
        ),
        source_action_plan=source_action_plan,
        task_profile_proposal="general_research",
        requires_need_discovery_enrichment=False,
    )


def _validate_route_payload(
    payload: dict[str, Any],
    *,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
    deterministic_source_plan: SourceActionPlan | None,
    repository_hints,
) -> tuple[ConversationRouteDecision | None, list[dict[str, str]], list[str]]:
    recovery_reasons: list[str] = []
    known = {
        field_name: payload[field_name]
        for field_name in ConversationRouteDecision.model_fields
        if field_name in payload
    }
    if len(known) != len(payload):
        recovery_reasons.append("ignored_extra_fields")
    turn_payload = known.get("turn_gate")
    if not isinstance(turn_payload, dict):
        return None, [{"loc": "turn_gate", "type": "model_type"}], recovery_reasons
    context_user_text = "\n".join(
        str(item.get("content") or "")
        for item in transcript_tail or []
        if item.get("role") == "user"
    )
    turn_gate, turn_errors, turn_recovery = _validate_turn_gate_payload(
        turn_payload,
        user_input=user_input,
        context_user_text=context_user_text,
    )
    recovery_reasons.extend(turn_recovery)
    if turn_gate is None:
        return None, [
            {"loc": f"turn_gate.{error['loc']}", "type": error["type"]}
            for error in turn_errors
        ], recovery_reasons
    try:
        source_plan = (
            deterministic_source_plan
            if deterministic_source_plan is not None
            else SourceActionPlan.model_validate(known.get("source_action_plan"))
        )
        source_plan = validate_source_action_plan(source_plan, repository_hints=repository_hints)
        route = ConversationRouteDecision.model_validate({
            **known,
            "turn_gate": turn_gate,
            "source_action_plan": source_plan,
        })
    except ValidationError as exc:
        return None, _validation_error_summary(exc), recovery_reasons

    exact_current = _exact_evidence(route.turn_gate.evidence_from_current_turn, user_input)
    exact_context = _exact_evidence(route.turn_gate.evidence_from_context, context_user_text)
    route = route.model_copy(update={
        "turn_gate": route.turn_gate.model_copy(update={
            "evidence_from_current_turn": exact_current,
            "evidence_from_context": exact_context,
            "task_profile_proposal": route.task_profile_proposal,
            "task_profile_evidence": route.task_profile_evidence,
            "suggested_task_title": route.suggested_task_title,
            "suggested_task_summary": route.suggested_task_summary,
            "requires_need_discovery_enrichment": route.requires_need_discovery_enrichment,
        }),
    })
    route = route.model_copy(update={
        "turn_gate": _validate_task_profile_proposal(
            route.turn_gate,
            user_input=user_input,
            context_user_text=context_user_text,
        ),
    })
    route = route.model_copy(update={
        "task_profile_proposal": route.turn_gate.task_profile_proposal,
        "task_profile_evidence": route.turn_gate.task_profile_evidence,
        "requires_need_discovery_enrichment": route.turn_gate.requires_need_discovery_enrichment,
    })
    if _requires_mutation_evidence(route.turn_gate) and not _mutation_evidence_matches(
        route.turn_gate,
        user_input,
    ):
        recovery_reasons.append("missing_exact_mutation_evidence")
        safe = conservative_conversation_route(
            source_action_plan=source_plan,
            reason="Mutating route lacked the complete current-turn mutation evidence.",
        )
        return safe, [], recovery_reasons
    if not _requires_mutation_evidence(route.turn_gate):
        route = route.model_copy(update={
            "turn_gate": route.turn_gate.model_copy(
                update={"mutation_evidence_from_current_turn": None}
            ),
        })
    route = route.model_copy(update={
        "turn_gate": _validate_turn_gate_decision(route.turn_gate),
    })
    return route, [], recovery_reasons


def _build_conversation_route_messages(
    *,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
    existing_contract_draft: dict[str, Any] | None,
    source_registry: list[dict[str, Any]],
    pending_jobs: list[dict[str, Any]],
    created_sources: list[dict[str, Any]],
    created_jobs: list[dict[str, Any]],
    answerability: dict[str, Any],
    deterministic_source_plan: SourceActionPlan | None,
    repository_hints: list[dict[str, Any]],
) -> list[dict[str, str]]:
    system = PromptSelector().build_system_prompt_for_v2_component("conversation_router")
    schema = (
        "Return exactly one JSON object and no Markdown. It must validate against this JSON Schema:\n"
        + json.dumps(ConversationRouteDecision.model_json_schema(), ensure_ascii=False, sort_keys=True)
        + "\nFor any mutating contract or confirmation action, mutation_evidence_from_current_turn must contain "
        "the complete current user message copied verbatim, with identical internal spaces, case, and punctuation. "
        "task_profile_evidence and evidence_from_current_turn never authorize mutation."
        + "\nValid ordinary-chat example:\n"
        + json.dumps(_ordinary_route_example(), ensure_ascii=False, sort_keys=True)
        + "\nValid research-update example:\n"
        + json.dumps(_contract_route_example(), ensure_ascii=False, sort_keys=True)
        + "\nValid correction-to-a-new-research-direction example:\n"
        + json.dumps(_research_correction_route_example(), ensure_ascii=False, sort_keys=True)
    )
    context = {
        "transcript_tail": transcript_tail or [],
        "existing_contract_draft": existing_contract_draft or {},
        "source_registry": source_registry,
        "pending_jobs": pending_jobs,
        "created_sources": created_sources,
        "created_jobs": created_jobs,
        "answerability": answerability,
        "deterministic_source_plan": (
            deterministic_source_plan.model_dump(mode="json")
            if deterministic_source_plan is not None else None
        ),
        "tool_capabilities": [
            capability.model_dump(mode="json") for capability in default_tool_capabilities()
        ],
        "repository_hints": repository_hints,
    }
    return [
        {"role": "system", "content": system},
        {"role": "system", "content": schema},
        {"role": "system", "content": "Context JSON:\n" + json.dumps(context, ensure_ascii=False, sort_keys=True)},
        {"role": "user", "content": user_input},
    ]


def _ordinary_route_example() -> dict[str, Any]:
    return {
        "turn_gate": {
            "turn_type": "ordinary_chat",
            "contract_action": "answer_without_contract_update",
            "contract_update_allowed": False,
            "need_discovery_allowed": False,
            "save_draft_allowed": False,
            "confirmation_action_proposal": "none",
            "task_profile_proposal": "general_research",
            "task_profile_evidence": None,
            "requires_need_discovery_enrichment": False,
            "suggested_task_title": None,
            "suggested_task_summary": None,
            "user_intent_summary": "ordinary conversation",
            "evidence_from_current_turn": [],
            "evidence_from_context": [],
            "mutation_evidence_from_current_turn": None,
            "confidence": 0.9,
            "reason": "No research contract change.",
            "next_reply_instruction": None,
        },
        "source_action_plan": {
            "actions": [], "user_visible_summary": "", "confidence": 1.0, "reason": "No source action."
        },
        "task_profile_proposal": "general_research",
        "task_profile_evidence": None,
        "suggested_task_title": None,
        "suggested_task_summary": None,
        "requires_need_discovery_enrichment": False,
    }


def _contract_route_example() -> dict[str, Any]:
    user_message = "我想以 PatchCore 为 baseline，在 MVTec AD 上提升 image-level AUROC。"
    example = _ordinary_route_example()
    example.update({
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "PatchCore",
        "suggested_task_title": "PatchCore MVTec AUROC优化",
        "suggested_task_summary": "提升 MVTec AD 的图像级 AUROC。",
    })
    example["turn_gate"].update({
        "turn_type": "contract_update",
        "contract_action": "update_contract",
        "contract_update_allowed": True,
        "need_discovery_allowed": True,
        "save_draft_allowed": True,
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "PatchCore",
        "suggested_task_title": "PatchCore MVTec AUROC优化",
        "suggested_task_summary": "提升 MVTec AD 的图像级 AUROC。",
        "user_intent_summary": "PatchCore experiment improvement",
        "evidence_from_current_turn": ["PatchCore"],
        "mutation_evidence_from_current_turn": user_message,
        "reason": "The user supplied research-contract evidence.",
    })
    return example


def _research_correction_route_example() -> dict[str, Any]:
    user_message = "不是要继续调分类模型，我要先诊断 Rust 服务在高并发下的内存泄漏，只做定位，不改代码。"
    direction_evidence = "诊断 Rust 服务在高并发下的内存泄漏"
    return {
        "turn_gate": {
            "turn_type": "contract_update",
            "contract_action": "update_contract",
            "contract_update_allowed": True,
            "need_discovery_allowed": True,
            "save_draft_allowed": True,
            "task_profile_proposal": "code_diagnosis",
            "task_profile_evidence": direction_evidence,
            "requires_need_discovery_enrichment": True,
            "evidence_from_current_turn": [user_message],
            "mutation_evidence_from_current_turn": user_message,
        },
        "source_action_plan": {},
        "task_profile_proposal": "code_diagnosis",
        "task_profile_evidence": direction_evidence,
        "suggested_task_title": None,
        "suggested_task_summary": None,
        "requires_need_discovery_enrichment": True,
    }


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _exact_evidence(candidates: list[str], text: str) -> list[str]:
    result: list[str] = []
    for candidate in candidates:
        quote = str(candidate).strip()
        if quote and quote in text and quote not in result:
            result.append(quote)
    return result


def _validation_error_summary(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "loc": ".".join(str(part) for part in error.get("loc", ())) or "root",
            "type": str(error.get("type") or "validation_error"),
        }
        for error in exc.errors()
    ]
