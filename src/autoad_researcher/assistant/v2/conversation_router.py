"""Unified semantic Router for source actions, contract gating, and task hints."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
    _validate_task_profile_proposal,
)


TaskProfileProposal = Literal[
    "empirical_model_research",
    "systems_optimization",
    "code_diagnosis",
    "general_research",
]

ConversationIntent = Literal[
    "research_planning",
    "research_question",
    "source_request",
    "ordinary_conversation",
    "joke",
    "frustration",
    "identity_question",
    "ambiguous",
]


class ContractMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: bool
    full_turn_mutation_evidence: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: bool
    action: Literal["none", "request_pending", "suspend", "resume", "supersede"] = "none"
    full_turn_mutation_evidence: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""

    @model_validator(mode="after")
    def _consistent_request(self) -> "ConfirmationRequest":
        if self.requested == (self.action == "none"):
            raise ValueError("requested and action must describe the same confirmation intent")
        return self


class TaskIdentityProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_title: str | None = None
    suggested_summary: str | None = None


class ConversationRouteDecision(BaseModel):
    """Orthogonal source, conversation, mutation, and confirmation decisions."""

    model_config = ConfigDict(extra="forbid")

    source_action_plan: SourceActionPlan
    conversation_intents: list[ConversationIntent] = Field(min_length=1)
    contract_mutation_request: ContractMutationRequest
    confirmation_request: ConfirmationRequest
    task_identity_proposal: TaskIdentityProposal = Field(default_factory=TaskIdentityProposal)
    task_profile_proposal: TaskProfileProposal
    task_profile_evidence: str | None = None
    requires_need_discovery_enrichment: bool

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_turn_gate(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "turn_gate" not in value:
            return value
        migrated = dict(value)
        gate = migrated.pop("turn_gate")
        if not isinstance(gate, dict):
            return migrated
        turn_type = str(gate.get("turn_type") or "ambiguous")
        valid_turn_types = {
            "contract_update", "contract_confirmation", "contract_question", "source_intake",
            "ordinary_chat", "joke", "frustration", "identity_question", "ambiguous",
        }
        valid_contract_actions = {
            "update_contract", "confirm_contract", "answer_without_contract_update", "ask_clarifying_question",
        }
        if turn_type not in valid_turn_types or str(gate.get("contract_action")) not in valid_contract_actions:
            raise ValueError("invalid legacy TurnGateDecision enum")
        intent_map = {
            "contract_update": "research_planning",
            "contract_confirmation": "research_planning",
            "contract_question": "research_question",
            "source_intake": "source_request",
            "ordinary_chat": "ordinary_conversation",
            "joke": "joke",
            "frustration": "frustration",
            "identity_question": "identity_question",
            "ambiguous": "ambiguous",
        }
        contract_action = str(gate.get("contract_action") or "answer_without_contract_update")
        confirmation_action = str(gate.get("confirmation_action_proposal") or "none")
        evidence = gate.get("mutation_evidence_from_current_turn")
        migrated.setdefault("conversation_intents", [intent_map.get(turn_type, "ambiguous")])
        migrated.setdefault("contract_mutation_request", {
            "requested": contract_action == "update_contract",
            "full_turn_mutation_evidence": evidence if contract_action == "update_contract" else None,
            "confidence": gate.get("confidence", 0.0),
            "rationale": gate.get("reason", ""),
        })
        requested_confirmation = contract_action == "confirm_contract" or confirmation_action != "none"
        migrated.setdefault("confirmation_request", {
            "requested": requested_confirmation,
            "action": "request_pending" if contract_action == "confirm_contract" else confirmation_action,
            "full_turn_mutation_evidence": evidence if requested_confirmation else None,
            "confidence": gate.get("confidence", 0.0),
            "rationale": gate.get("reason", ""),
        })
        migrated.setdefault("task_identity_proposal", {
            "suggested_title": migrated.pop("suggested_task_title", None),
            "suggested_summary": migrated.pop("suggested_task_summary", None),
        })
        return migrated

    @property
    def suggested_task_title(self) -> str | None:
        return self.task_identity_proposal.suggested_title

    @property
    def suggested_task_summary(self) -> str | None:
        return self.task_identity_proposal.suggested_summary

    @property
    def turn_gate(self) -> TurnGateDecision:
        return _compatibility_turn_gate(self)


def _compatibility_turn_gate(route: ConversationRouteDecision) -> TurnGateDecision:
    confirmation = route.confirmation_request
    mutation = route.contract_mutation_request
    if mutation.requested:
        turn_type = "contract_update"
        contract_action = "update_contract"
    elif confirmation.requested:
        turn_type = "contract_confirmation"
        contract_action = "confirm_contract" if confirmation.action == "request_pending" else "answer_without_contract_update"
    else:
        intent_priority = (
            ("research_question", "contract_question"),
            ("source_request", "source_intake"),
            ("frustration", "frustration"),
            ("identity_question", "identity_question"),
            ("joke", "joke"),
            ("ordinary_conversation", "ordinary_chat"),
            ("ambiguous", "ambiguous"),
        )
        turn_type = next(
            (legacy for intent, legacy in intent_priority if intent in route.conversation_intents),
            "ordinary_chat",
        )
        contract_action = "ask_clarifying_question" if turn_type == "ambiguous" else "answer_without_contract_update"
    confirmation_action = (
        confirmation.action
        if confirmation.requested and confirmation.action in {"suspend", "resume", "supersede"}
        else "none"
    )
    evidence = (
        mutation.full_turn_mutation_evidence
        if mutation.requested
        else confirmation.full_turn_mutation_evidence if confirmation.requested else None
    )
    confidence = max(mutation.confidence, confirmation.confidence)
    return TurnGateDecision(
        turn_type=turn_type,
        contract_action=contract_action,
        contract_update_allowed=mutation.requested,
        need_discovery_allowed=mutation.requested,
        save_draft_allowed=mutation.requested,
        confirmation_action_proposal=confirmation_action,
        task_profile_proposal=route.task_profile_proposal,
        task_profile_evidence=route.task_profile_evidence,
        requires_need_discovery_enrichment=route.requires_need_discovery_enrichment,
        suggested_task_title=route.suggested_task_title,
        suggested_task_summary=route.suggested_task_summary,
        user_intent_summary=", ".join(route.conversation_intents),
        evidence_from_current_turn=[evidence] if evidence else [],
        mutation_evidence_from_current_turn=evidence,
        confidence=confidence,
        reason=mutation.rationale or confirmation.rationale,
    )


def _route_mutation_evidence_matches(route: ConversationRouteDecision, user_input: str) -> bool:
    requests = []
    if route.contract_mutation_request.requested:
        requests.append(route.contract_mutation_request.full_turn_mutation_evidence)
    if route.confirmation_request.requested:
        requests.append(route.confirmation_request.full_turn_mutation_evidence)
    return all((evidence or "").strip() == user_input.strip() for evidence in requests)


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
        conversation_intents=["ordinary_conversation"],
        contract_mutation_request=ContractMutationRequest(
            requested=False, confidence=0.0, rationale=reason,
        ),
        confirmation_request=ConfirmationRequest(
            requested=False, action="none", confidence=0.0, rationale=reason,
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
        conversation_intents=["source_request"],
        contract_mutation_request=ContractMutationRequest(
            requested=False,
            confidence=1.0,
            rationale="Structured source input is handled deterministically.",
        ),
        confirmation_request=ConfirmationRequest(
            requested=False,
            action="none",
            confidence=1.0,
            rationale="Structured source input is handled deterministically.",
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
    accepted_fields = {*ConversationRouteDecision.model_fields, "turn_gate", "suggested_task_title", "suggested_task_summary"}
    known = {
        field_name: payload[field_name]
        for field_name in accepted_fields
        if field_name in payload
    }
    if len(known) != len(payload):
        recovery_reasons.append("ignored_extra_fields")
    context_user_text = "\n".join(
        str(item.get("content") or "")
        for item in transcript_tail or []
        if item.get("role") == "user"
    )
    try:
        source_plan = (
            deterministic_source_plan
            if deterministic_source_plan is not None
            else SourceActionPlan.model_validate(known.get("source_action_plan"))
        )
        source_plan = validate_source_action_plan(source_plan, repository_hints=repository_hints)
        route = ConversationRouteDecision.model_validate({
            **known,
            "source_action_plan": source_plan,
        })
    except ValidationError as exc:
        return None, _validation_error_summary(exc), recovery_reasons

    validated_gate = _validate_task_profile_proposal(
            route.turn_gate,
            user_input=user_input,
            context_user_text=context_user_text,
        )
    route = route.model_copy(update={
        "task_profile_proposal": validated_gate.task_profile_proposal,
        "task_profile_evidence": validated_gate.task_profile_evidence,
        "requires_need_discovery_enrichment": validated_gate.requires_need_discovery_enrichment,
    })
    if not _route_mutation_evidence_matches(route, user_input):
        recovery_reasons.append("missing_exact_mutation_evidence")
        safe = conservative_conversation_route(
            source_action_plan=source_plan,
            reason="Mutating route lacked the complete current-turn mutation evidence.",
        )
        return safe, [], recovery_reasons
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
        + "\nSource actions, conversation intents, contract mutation, and confirmation are orthogonal; a turn may request more than one. "
        "For requested contract mutation or confirmation, full_turn_mutation_evidence must contain the complete current user message "
        "copied verbatim, with identical internal spaces, case, and punctuation. task_profile_evidence never authorizes mutation."
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
        "source_action_plan": {
            "actions": [], "user_visible_summary": "", "confidence": 1.0, "reason": "No source action."
        },
        "conversation_intents": ["ordinary_conversation"],
        "contract_mutation_request": {
            "requested": False, "full_turn_mutation_evidence": None, "confidence": 0.9,
            "rationale": "No research contract change."
        },
        "confirmation_request": {
            "requested": False, "action": "none", "full_turn_mutation_evidence": None,
            "confidence": 0.9, "rationale": "No confirmation request."
        },
        "task_identity_proposal": {"suggested_title": None, "suggested_summary": None},
        "task_profile_proposal": "general_research",
        "task_profile_evidence": None,
        "requires_need_discovery_enrichment": False,
    }


def _contract_route_example() -> dict[str, Any]:
    user_message = "我想复现 Library-A 的排序基准，保持官方评测协议，只生成实验计划。"
    example = _ordinary_route_example()
    example.update({
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "复现 Library-A 的排序基准",
        "task_identity_proposal": {
            "suggested_title": "Library-A 排序基准复现",
            "suggested_summary": "按官方评测协议复现排序基准。",
        },
        "conversation_intents": ["research_planning"],
        "contract_mutation_request": {
            "requested": True,
            "full_turn_mutation_evidence": user_message,
            "confidence": 0.9,
            "rationale": "The user supplied research-contract evidence.",
        },
    })
    return example


def _research_correction_route_example() -> dict[str, Any]:
    user_message = "不是要继续调分类模型，我要先诊断 Rust 服务在高并发下的内存泄漏，只做定位，不改代码。"
    direction_evidence = "诊断 Rust 服务在高并发下的内存泄漏"
    return {
        "source_action_plan": {},
        "conversation_intents": ["research_planning"],
        "contract_mutation_request": {
            "requested": True, "full_turn_mutation_evidence": user_message,
            "confidence": 0.9, "rationale": "The user corrected the research direction."
        },
        "confirmation_request": {
            "requested": False, "action": "none", "full_turn_mutation_evidence": None,
            "confidence": 0.9, "rationale": "No confirmation request."
        },
        "task_identity_proposal": {"suggested_title": None, "suggested_summary": None},
        "task_profile_proposal": "code_diagnosis",
        "task_profile_evidence": direction_evidence,
        "requires_need_discovery_enrichment": True,
    }


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _validation_error_summary(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "loc": ".".join(str(part) for part in error.get("loc", ())) or "root",
            "type": str(error.get("type") or "validation_error"),
        }
        for error in exc.errors()
    ]
