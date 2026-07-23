"""Two-call research decision and reply agents with shared context."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from autoad_researcher.assistant.model_routing import ModelRoute
from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.model_routing import ModelRoute
from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.research_intent_summary import (
    BasedStatement,
    ConfirmedTaskParameters,
    ResearchIntentSummary,
)
from autoad_researcher.assistant.v2.task_bridge import TaskInstruction
from autoad_researcher.assistant.v2.target_adapter import get_target_adapter_registry
from autoad_researcher.schemas.decisions import ConfirmedDecision


T = TypeVar("T")


class SourceInstruction(BaseModel):
    """A typed candidate action against one registered source."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["request_source_removal", "request_source_reparse"]
    source_id: str = Field(min_length=1)
    label_hint: str = ""
    reason: str = ""


class DatasetSourceInstruction(BaseModel):
    """An explicit server-local dataset directory supplied by the user."""

    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1)
    user_label: str = Field(min_length=1)


class TargetSpec(BaseModel):
    """Semantic benchmark selector proposed for deterministic validation."""

    model_config = ConfigDict(extra="forbid")

    adapter_id: str = Field(min_length=1)
    selectors: dict[str, Any]


DialogueMode = Literal["ask", "plan", "act", "act_request", "reject"]
ActionScope = Literal["none", "source", "repository", "code", "experiment", "system"]
DialoguePolicy = Literal["allow", "ask_permission", "deny"]
EvidenceStatus = Literal["sufficient", "insufficient", "conflicting", "unavailable"]
ConversationTransition = Literal["new", "continue", "revise", "confirm", "cancel"]
Feasibility = Literal["not_assessed", "feasible", "infeasible_as_stated"]
TaskActionProposal = Literal[
    "prepare_experiment_task",
    "confirm_pending_plan_only_task",
]
PolicyCategory = Literal[
    "none",
    "evaluation_leakage",
    "evaluation_manipulation",
    "evidence_falsification",
    "evidence_destruction",
    "unsafe_operation",
]
ExecutionGate = Literal[
    "not_requested",
    "blocked_missing_contract",
    "blocked_dialogue_only",
]


class ResearchPolicyAssessment(BaseModel):
    """Semantic research-policy proposal from the Decision Agent."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "reject"]
    category: PolicyCategory
    reason: str
    safe_alternative: str

    @model_validator(mode="after")
    def _validate_decision(self) -> "ResearchPolicyAssessment":
        if self.decision == "allow":
            if self.category != "none":
                raise ValueError("allow policy decision requires category=none")
            if self.reason or self.safe_alternative:
                raise ValueError("allow policy decision must not carry refusal details")
        else:
            if self.category == "none":
                raise ValueError("reject policy decision requires a policy category")
            if not self.reason.strip():
                raise ValueError("reject policy decision requires a reason")
            if not self.safe_alternative.strip():
                raise ValueError("reject policy decision requires a safe alternative")
        return self


class DialogueDecision(BaseModel):
    """Small semantic proposal: mode, policy, and candidate actions only."""

    model_config = ConfigDict(extra="forbid")

    dialogue_mode: DialogueMode
    action_scope: ActionScope = "none"
    policy: DialoguePolicy = "allow"
    evidence_status: EvidenceStatus = "unavailable"
    conversation_transition: ConversationTransition = "new"
    feasibility: Feasibility = "not_assessed"
    numeric_claim_allowed: bool = True
    policy_assessment: ResearchPolicyAssessment
    source_action: SourceInstruction | None = None
    dataset_source: DatasetSourceInstruction | None = None
    task_action: TaskActionProposal | None = None
    target_spec: TargetSpec | None = None
    _is_valid: bool = PrivateAttr(default=False)

    @property
    def is_valid(self) -> bool:
        return self._is_valid

    @model_validator(mode="after")
    def _align_legacy_policy_assessment(self) -> "DialogueDecision":
        if self.policy_assessment.decision == "reject":
            self.policy = "deny"
        return self


class GatedDialogueDecision(BaseModel):
    """Decision after deterministic state, identifier, and permission checks."""

    model_config = ConfigDict(extra="forbid")

    dialogue_mode: DialogueMode
    action_scope: ActionScope = "none"
    policy: DialoguePolicy = "allow"
    evidence_status: EvidenceStatus = "unavailable"
    conversation_transition: ConversationTransition = "new"
    feasibility: Feasibility = "not_assessed"
    numeric_claim_allowed: bool = True
    policy_assessment: ResearchPolicyAssessment
    source_action: SourceInstruction | None = None
    source_permission: dict[str, Any] | None = None
    dataset_source: DatasetSourceInstruction | None = None
    task_action: TaskInstruction | None = None
    target_spec: TargetSpec | None = None
    execution_gate: ExecutionGate = "not_requested"
    gate_notes: list[str] = Field(default_factory=list)


class RawTaskParameters(BaseModel):
    """Flat, model-facing parameter updates without authoritative provenance."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    baseline: str | None = Field(default=None, min_length=1)
    dataset: str | None = Field(default=None, min_length=1)
    compute_budget: str | None = Field(default=None, min_length=1)
    primary_metrics: list[str] | None = None
    evaluation_constraints: list[str] | None = None

    @field_validator("primary_metrics", "evaluation_constraints")
    @classmethod
    def _reject_empty_list_values(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and any(not value for value in values):
            raise ValueError("task parameter values must not be empty")
        return values


class ResearchReplySummaryDraft(BaseModel):
    """Model-facing summary shape; provenance is added by trusted code."""

    model_config = ConfigDict(extra="forbid")

    goal: str = ""
    confirmed_facts: list[str] = Field(default_factory=list)
    confirmed_task_parameters: RawTaskParameters = Field(default_factory=RawTaskParameters)
    inferred_facts: list[BasedStatement] = Field(default_factory=list)
    unresolved_conflicts: list[BasedStatement] = Field(default_factory=list)
    blocking_question: str | None = None


class ResearchReplyDraftResponse(BaseModel):
    """One LLM reply with a flat task-parameter transport contract."""

    model_config = ConfigDict(extra="forbid")

    reply_to_user: str = Field(min_length=1)
    summary: ResearchReplySummaryDraft


class ResearchReplyResponse(BaseModel):
    """Natural-language reply and authoritative persisted summary."""

    model_config = ConfigDict(extra="forbid")

    reply_to_user: str = Field(min_length=1)
    summary: ResearchIntentSummary
    _should_persist: bool = PrivateAttr(default=False)

    @property
    def should_persist(self) -> bool:
        return self._should_persist

    def visible_reply(self) -> str:
        return self.reply_to_user.strip()


class ResearchDecisionAgent:
    """Classify semantics and propose candidate actions in one small LLM call."""

    @classmethod
    def decide(
        cls,
        *,
        run_dir: Path | None = None,
        user_input: str,
        evidence_state: dict[str, Any],
        last_summary: ResearchIntentSummary | None,
        transcript_tail: list[dict[str, Any]] | None = None,
        api_key: str = "",
        provider_url: str = "",
        model: str = "",
        temperature: float = 0.0,
        model_route: ModelRoute | None = None,
    ) -> DialogueDecision:
        if not api_key or not model.strip():
            return _fallback_decision()
        messages = cls.build_messages(
            user_input=user_input,
            evidence_state=evidence_state,
            last_summary=last_summary,
            transcript_tail=transcript_tail,
        )
        decision = _call_with_schema_repair(
            run_dir=run_dir,
            repair_event_type="assistant.decision_repair",
            api_key=api_key,
            provider_url=provider_url,
            messages=messages,
            model=model,
            temperature=temperature,
            model_route=model_route,
            validate_reply=_validate_decision_reply,
            repair_messages=_decision_repair_messages,
        )
        return decision or _fallback_decision()

    @classmethod
    def build_messages(
        cls,
        *,
        user_input: str,
        evidence_state: dict[str, Any],
        last_summary: ResearchIntentSummary | None,
        transcript_tail: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        runtime_context = _runtime_context(
            evidence_state=evidence_state,
            last_summary=last_summary,
            transcript_tail=transcript_tail,
            include_adapters=True,
        )
        contract = PromptSelector().build_research_decision_prompt()
        return [
            {"role": "system", "content": runtime_context + "\n\n" + contract},
            {"role": "user", "content": user_input},
        ]


def _validate_decision_reply(
    reply: str,
) -> tuple[DialogueDecision | None, dict[str, Any]]:
    payload = _parse_json_object(reply)
    if payload is None:
        return None, {"failure_kind": "json_parse_error", "validation_errors": []}
    try:
        decision = DialogueDecision.model_validate(payload)
    except ValidationError as exc:
        return None, {
            "failure_kind": "schema_validation_error",
            "validation_errors": _compact_validation_errors(exc),
        }
    decision._is_valid = True
    return decision, {}


def _compact_validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for error in exc.errors(include_url=False)[:8]:
        location = error.get("loc", ())
        path = ".".join(str(part) for part in location) or "$"
        errors.append({"path": path, "type": str(error.get("type") or "unknown")})
    return errors


def _decision_repair_messages(
    messages: list[dict[str, str]],
    raw_reply: str,
    failure: dict[str, Any],
) -> list[dict[str, str]]:
    validation_errors = json.dumps(
        failure.get("validation_errors") or [],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [
        *messages,
        {"role": "assistant", "content": raw_reply[:4000]},
        {
            "role": "user",
            "content": (
                "上一个回答未通过 DialogueDecision schema 校验。"
                f"失败类型：{failure.get('failure_kind', 'unknown')}。"
                f"字段问题：{validation_errors}。\n"
                "保持上一轮的语义判断不变，只修复 JSON 结构。"
                "仅输出一个符合 decision_output schema 的 JSON object。"
                "不要解释，不要 Markdown，不要代码围栏。"
            ),
        },
    ]


def _call_with_schema_repair(
    *,
    run_dir: Path | None,
    repair_event_type: str,
    api_key: str,
    provider_url: str,
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    model_route: ModelRoute | None,
    validate_reply: Callable[[str], tuple[T | None, dict[str, Any]]],
    repair_messages: Callable[
        [list[dict[str, str]], str, dict[str, Any]],
        list[dict[str, str]],
    ],
) -> T | None:
    """Validate one schema-bound answer, then allow one diagnostic repair call."""
    from autoad_researcher.ui.chat_client import call_research_chat

    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model_route.model_id if model_route is not None else model,
        timeout_s=30,
        priority="interactive",
        response_format_json=True,
        temperature=temperature,
        thinking_type=model_route.thinking_type if model_route is not None else None,
        reasoning_effort=model_route.reasoning_effort if model_route is not None else None,
    )
    if result.get("error"):
        return None
    raw_reply = str(result.get("reply") or "")
    if not raw_reply.strip():
        return None
    value, failure = validate_reply(raw_reply)
    if value is not None:
        return value

    repair_result = call_research_chat(
        api_key,
        provider_url,
        repair_messages(messages, raw_reply, failure),
        model=model_route.model_id if model_route is not None else model,
        timeout_s=30,
        priority="interactive",
        response_format_json=True,
        temperature=0.0,
        thinking_type=model_route.thinking_type if model_route is not None else None,
        reasoning_effort=model_route.reasoning_effort if model_route is not None else None,
    )
    repair_raw_reply = str(repair_result.get("reply") or "")
    repaired, _ = (
        validate_reply(repair_raw_reply)
        if not repair_result.get("error") and repair_raw_reply.strip()
        else (None, {})
    )
    _record_schema_repair(
        run_dir,
        event_type=repair_event_type,
        original_reply=raw_reply,
        failure=failure,
        outcome="succeeded" if repaired is not None else "failed",
    )
    return repaired


def _record_schema_repair(
    run_dir: Path | None,
    *,
    event_type: str,
    original_reply: str,
    failure: dict[str, Any],
    outcome: Literal["succeeded", "failed"],
) -> None:
    if run_dir is None:
        return
    append_event(
        run_dir,
        event_type,
        {
            "attempted": True,
            "outcome": outcome,
            "failure_kind": failure.get("failure_kind", "unknown"),
            "validation_errors": failure.get("validation_errors") or [],
            "raw_output_length": len(original_reply),
            "raw_output_sha256": sha256(original_reply.encode("utf-8")).hexdigest(),
            "repair_call_count": 1,
        },
    )


class ResearchReplyAgent:
    """Write the user reply and complete summary from one frozen decision."""

    @classmethod
    def respond(
        cls,
        *,
        run_dir: Path | None = None,
        user_input: str,
        evidence_state: dict[str, Any],
        frozen_decision: GatedDialogueDecision,
        last_summary: ResearchIntentSummary | None,
        transcript_tail: list[dict[str, Any]] | None = None,
        api_key: str = "",
        provider_url: str = "",
        model: str = "",
        temperature: float = 0.0,
        model_route: ModelRoute | None = None,
        on_reply_delta: Callable[[str], None] | None = None,
    ) -> ResearchReplyResponse:
        if not api_key:
            return _fallback_reply(
                last_summary,
                "当前没有可用的对话模型连接，材料任务仍可在后台处理。",
            )
        if not model.strip():
            return _fallback_reply(
                last_summary,
                "当前没有配置对话模型，材料任务仍可在后台处理。",
            )
        messages = cls.build_messages(
            user_input=user_input,
            evidence_state=evidence_state,
            frozen_decision=frozen_decision,
            last_summary=last_summary,
            transcript_tail=transcript_tail,
        )
        response = _call_with_schema_repair(
            run_dir=run_dir,
            repair_event_type="assistant.reply_repair",
            api_key=api_key,
            provider_url=provider_url,
            messages=messages,
            model=model,
            temperature=temperature,
            model_route=model_route,
            validate_reply=lambda reply: _validate_reply_response(
                reply,
                user_input=user_input,
                frozen_decision=frozen_decision,
                last_summary=last_summary,
            ),
            repair_messages=_reply_repair_messages,
        )
        if response is None:
            return _fallback_reply(last_summary, "这轮回复生成失败了，请重试。")
        response._should_persist = True
        if on_reply_delta is not None:
            on_reply_delta(response.visible_reply())
        return response

    @classmethod
    def build_messages(
        cls,
        *,
        user_input: str,
        evidence_state: dict[str, Any],
        frozen_decision: GatedDialogueDecision,
        last_summary: ResearchIntentSummary | None,
        transcript_tail: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        runtime_context = _runtime_context(
            evidence_state=evidence_state,
            last_summary=last_summary,
            transcript_tail=transcript_tail,
            include_adapters=False,
        )
        frozen = _json_text(frozen_decision.model_dump(mode="json"))
        contract = PromptSelector().build_research_reply_prompt()
        system = (
            runtime_context
            + f"\n冻结决策（不可改写）：{frozen}\n\n"
            + contract
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_input},
        ]


def _validate_reply_response(
    reply: str,
    *,
    user_input: str,
    frozen_decision: GatedDialogueDecision,
    last_summary: ResearchIntentSummary | None,
) -> tuple[ResearchReplyResponse | None, dict[str, Any]]:
    payload = _parse_json_object(reply)
    if payload is None:
        return None, {"failure_kind": "json_parse_error", "validation_errors": []}
    try:
        draft = ResearchReplyDraftResponse.model_validate(payload)
    except ValidationError as exc:
        return None, {
            "failure_kind": "schema_validation_error",
            "validation_errors": _compact_validation_errors(exc),
        }
    return ResearchReplyResponse(
        reply_to_user=draft.reply_to_user,
        summary=_materialize_reply_summary(
            draft.summary,
            user_input=user_input,
            frozen_decision=frozen_decision,
            last_summary=last_summary,
        ),
    ), {}


def _materialize_reply_summary(
    draft: ResearchReplySummaryDraft,
    *,
    user_input: str,
    frozen_decision: GatedDialogueDecision,
    last_summary: ResearchIntentSummary | None,
) -> ResearchIntentSummary:
    """Attach provenance in code instead of asking the reply model to invent it."""
    previous_parameters = (
        last_summary.confirmed_task_parameters
        if last_summary is not None
        else ConfirmedTaskParameters()
    )
    return ResearchIntentSummary(
        goal=draft.goal,
        confirmed_facts=draft.confirmed_facts,
        confirmed_task_parameters=_materialize_task_parameters(
            draft.confirmed_task_parameters,
            previous_parameters=previous_parameters,
            user_input=user_input,
            frozen_decision=frozen_decision,
        ),
        inferred_facts=draft.inferred_facts,
        unresolved_conflicts=draft.unresolved_conflicts,
        blocking_question=draft.blocking_question,
    )


def _materialize_task_parameters(
    raw: RawTaskParameters,
    *,
    previous_parameters: ConfirmedTaskParameters,
    user_input: str,
    frozen_decision: GatedDialogueDecision,
) -> ConfirmedTaskParameters:
    """Merge flat current-turn updates with trusted persisted provenance."""
    if frozen_decision.policy != "allow" or frozen_decision.conversation_transition == "cancel":
        return previous_parameters
    source = (
        "user_confirmed"
        if frozen_decision.conversation_transition == "confirm"
        else "user_provided"
    )
    evidence = f"当前用户消息：{user_input.strip()}"
    return ConfirmedTaskParameters(
        baseline=_materialize_scalar_parameter(
            raw.baseline,
            previous_parameters.baseline,
            source=source,
            evidence=evidence,
        ),
        dataset=_materialize_scalar_parameter(
            raw.dataset,
            previous_parameters.dataset,
            source=source,
            evidence=evidence,
        ),
        compute_budget=_materialize_scalar_parameter(
            raw.compute_budget,
            previous_parameters.compute_budget,
            source=source,
            evidence=evidence,
        ),
        primary_metrics=_materialize_list_parameter(
            raw.primary_metrics,
            previous_parameters.primary_metrics,
            source=source,
            evidence=evidence,
        ),
        evaluation_constraints=_materialize_list_parameter(
            raw.evaluation_constraints,
            previous_parameters.evaluation_constraints,
            source=source,
            evidence=evidence,
        ),
    )


def _materialize_scalar_parameter(
    value: str | None,
    previous: ConfirmedDecision | None,
    *,
    source: Literal["user_provided", "user_confirmed"],
    evidence: str,
) -> ConfirmedDecision | None:
    if value is None or (previous is not None and previous.value == value):
        return previous
    return ConfirmedDecision(value=value, source=source, evidence=evidence)


def _materialize_list_parameter(
    values: list[str] | None,
    previous: list[ConfirmedDecision],
    *,
    source: Literal["user_provided", "user_confirmed"],
    evidence: str,
) -> list[ConfirmedDecision]:
    if values is None or [item.value for item in previous] == values:
        return previous
    return [ConfirmedDecision(value=value, source=source, evidence=evidence) for value in values]


def _reply_repair_messages(
    messages: list[dict[str, str]],
    raw_reply: str,
    failure: dict[str, Any],
) -> list[dict[str, str]]:
    validation_errors = json.dumps(
        failure.get("validation_errors") or [],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [
        *messages,
        {"role": "assistant", "content": raw_reply[:4000]},
        {
            "role": "user",
            "content": (
                "上一个回答未通过 ResearchReplyResponse schema 校验。"
                f"失败类型：{failure.get('failure_kind', 'unknown')}。"
                f"字段问题：{validation_errors}。\n"
                "保持冻结决策和上一轮语义不变，只修复 JSON 结构。"
                "仅输出一个符合回复 schema 的 JSON object。"
                "不要解释，不要 Markdown，不要代码围栏。"
            ),
        },
    ]


def _fallback_decision() -> DialogueDecision:
    return DialogueDecision(
        dialogue_mode="ask",
        policy_assessment=_allow_policy_assessment(),
    )


def _fallback_reply(
    last_summary: ResearchIntentSummary | None,
    message: str,
) -> ResearchReplyResponse:
    return ResearchReplyResponse(
        reply_to_user=message,
        summary=last_summary or ResearchIntentSummary(),
    )


def _allow_policy_assessment() -> ResearchPolicyAssessment:
    return ResearchPolicyAssessment(
        decision="allow",
        category="none",
        reason="",
        safe_alternative="",
    )


def _runtime_context(
    *,
    evidence_state: dict[str, Any],
    last_summary: ResearchIntentSummary | None,
    transcript_tail: list[dict[str, Any]] | None,
    include_adapters: bool,
) -> str:
    evidence = _json_text(_compact_evidence_state(evidence_state))
    previous = _json_text(
        (last_summary or ResearchIntentSummary()).model_dump(mode="json")
    )
    recent = _json_text(_clean_transcript(transcript_tail))
    lines = [
        "本轮上下文：",
        f"当前可用材料：{evidence}",
        f"上一轮研究摘要：{previous}",
        f"最近对话：{recent}",
    ]
    if include_adapters:
        adapters = _json_text(get_target_adapter_registry().prompt_catalog())
        lines.append(
            "系统支持的仓库目标 Adapter（能力目录，不表示当前已登记对应仓库）："
            + adapters
        )
    return "\n".join(lines)


def _clean_transcript(
    transcript_tail: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in (transcript_tail or [])[-8:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            cleaned.append({"role": role, "content": content})
    return cleaned


def _compact_evidence_state(state: dict[str, Any]) -> dict[str, Any]:
    usable: list[dict[str, Any]] = []
    for item in (state.get("usable_evidence") or [])[:12]:
        if not isinstance(item, dict):
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        usable.append({
            "source_id": item.get("source_id"),
            "evidence_type": item.get("evidence_type"),
            "artifact_path": item.get("artifact_path"),
            "parser_name": item.get("parser_name"),
            "summary": str(item.get("summary") or "")[:3000],
            "metadata": {
                key: raw.get(key)
                for key in (
                    "analysis_id",
                    "repository_commit",
                    "source_fingerprint",
                    "validation_status",
                    "formal_artifact_paths",
                    "entrypoint_candidates",
                    "configuration_candidates",
                    "declared_entrypoints",
                    "top_level_entries",
                    "compatibility_status",
                    "quality_level",
                    "warnings",
                    "fatal_errors",
                )
                if raw.get(key) is not None
            },
        })
    return {
        "usable_evidence": usable,
        "unparsed_sources": state.get("unparsed_sources") or [],
        "unusable_parsed_sources": state.get("unusable_parsed_sources") or [],
        "pending_jobs": state.get("pending_jobs") or [],
        "failed_jobs": state.get("failed_jobs") or [],
        "answerability": state.get("answerability") or {},
        "current_turn_material_actions": state.get("current_turn_material_actions") or {},
        "registered_sources": state.get("registered_sources") or [],
        "dialogue_state": state.get("dialogue_state") or {},
    }


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
