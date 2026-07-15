"""LLM-first, field-level interpretation of one authorized research turn."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autoad_researcher.assistant.llm_runtime import runtime_trace_fields
from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.v2.llm_trace_service import append_llm_trace
from autoad_researcher.assistant.v2.mutation_protocol import ContractMutationProposal
from autoad_researcher.assistant.v2.research_semantics import (
    AdvisorySuggestion,
    EvidenceConflict,
    OpenQuestion,
    ResearchModeAssessment,
)


MaterialTarget = Literal[
    "baseline_repo",
    "baseline_commit",
    "baseline_entrypoint",
    "baseline_config",
]


class MaterialObservationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: MaterialTarget
    proposed_value: Any
    evidence_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ResearchIntentInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_modes: ResearchModeAssessment
    intent_mutation: ContractMutationProposal
    material_observations: list[MaterialObservationProposal] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    evidence_conflicts: list[EvidenceConflict] = Field(default_factory=list)
    advisory_suggestions: list[AdvisorySuggestion] = Field(default_factory=list)


class InterpretationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "failed"]
    interpretation: ResearchIntentInterpretation | None = None
    failure_reason: Literal[
        "none",
        "provider_error",
        "non_json",
        "schema_error",
        "invalid_current_turn_provenance",
    ] = "none"


def interpret_research_intent(
    *,
    run_dir: Path,
    user_input: str,
    persisted_contract: dict[str, Any] | None,
    persisted_draft_sha256: str | None,
    recent_mutation_receipts: list[dict[str, Any]],
    recent_dialogue: list[dict[str, str]],
    active_sources: list[dict[str, Any]],
    usable_evidence: list[dict[str, Any]],
    unusable_evidence: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    pending_confirmation: dict[str, Any] | None,
    system_safety_policy: list[str],
    api_key: str,
    provider_url: str,
    model: str,
) -> InterpretationOutcome:
    """Interpret one current turn; never mutate the Draft or synthesize a fallback."""

    selector = PromptSelector()
    profile = selector.profile_for_v2_component("research_intent_interpreter")
    system_prompt = selector.build_system_prompt_for_v2_component("research_intent_interpreter")
    messages = _build_interpreter_messages(
        system_prompt=system_prompt,
        user_input=user_input,
        persisted_contract=persisted_contract,
        persisted_draft_sha256=persisted_draft_sha256,
        recent_mutation_receipts=recent_mutation_receipts,
        recent_dialogue=recent_dialogue,
        active_sources=active_sources,
        usable_evidence=usable_evidence,
        unusable_evidence=unusable_evidence,
        jobs=jobs,
        pending_confirmation=pending_confirmation,
        system_safety_policy=system_safety_policy,
    )
    from autoad_researcher.ui.chat_client import call_research_chat

    started = time.perf_counter()
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model,
        timeout_s=12,
        priority="interactive",
        response_format_json=True,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    raw_output = str(result.get("reply") or "")
    if result.get("error"):
        outcome = InterpretationOutcome(status="failed", failure_reason="provider_error")
        validation_errors: list[dict[str, str]] = []
    else:
        payload = _parse_json_object(raw_output)
        if payload is None:
            outcome = InterpretationOutcome(status="failed", failure_reason="non_json")
            validation_errors = []
        else:
            try:
                interpretation = ResearchIntentInterpretation.model_validate(payload)
            except ValidationError as exc:
                outcome = InterpretationOutcome(status="failed", failure_reason="schema_error")
                validation_errors = _validation_error_summary(exc)
            else:
                if not _current_turn_provenance_is_valid(interpretation, user_input):
                    outcome = InterpretationOutcome(
                        status="failed",
                        failure_reason="invalid_current_turn_provenance",
                    )
                    validation_errors = []
                else:
                    outcome = InterpretationOutcome(status="ok", interpretation=interpretation)
                    validation_errors = []
    append_llm_trace(
        run_dir,
        call_site="research_intent_interpreter",
        prompt_id=profile.prompt_id,
        prompt_version=profile.prompt_version,
        prompt_text=system_prompt,
        model=model,
        provider_url=provider_url,
        messages=messages,
        raw_output=raw_output,
        parse_status="ok" if outcome.status == "ok" else "error",
        schema_validation="ok" if outcome.status == "ok" else "error",
        schema_validation_errors=validation_errors,
        fallback_reason="" if outcome.status == "ok" else outcome.failure_reason,
        latency_ms=latency_ms,
        **runtime_trace_fields(result),
    )
    return outcome


def _build_interpreter_messages(
    *,
    system_prompt: str,
    user_input: str,
    persisted_contract: dict[str, Any] | None,
    persisted_draft_sha256: str | None,
    recent_mutation_receipts: list[dict[str, Any]],
    recent_dialogue: list[dict[str, str]],
    active_sources: list[dict[str, Any]],
    usable_evidence: list[dict[str, Any]],
    unusable_evidence: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    pending_confirmation: dict[str, Any] | None,
    system_safety_policy: list[str],
) -> list[dict[str, str]]:
    context = {
        "current_persisted_contract": persisted_contract,
        "current_draft_sha256": persisted_draft_sha256,
        "recent_mutation_receipts": recent_mutation_receipts[-3:],
        "recent_dialogue": recent_dialogue[-4:],
        "active_sources": active_sources,
        "usable_evidence": usable_evidence,
        "unusable_evidence": unusable_evidence,
        "jobs": jobs,
        "pending_confirmation": pending_confirmation,
        "system_safety_policy": system_safety_policy,
    }
    schema = json.dumps(ResearchIntentInterpretation.model_json_schema(), ensure_ascii=False, sort_keys=True)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": "Required JSON Schema:\n" + schema},
        {"role": "system", "content": "Persisted snapshot JSON:\n" + json.dumps(context, ensure_ascii=False, sort_keys=True)},
        {"role": "user", "content": user_input},
    ]


def _current_turn_provenance_is_valid(
    interpretation: ResearchIntentInterpretation,
    user_input: str,
) -> bool:
    proposal = interpretation.intent_mutation
    if proposal.full_turn_mutation_evidence.strip() != user_input.strip():
        return False
    return all(
        span.end <= len(user_input) and user_input[span.start:span.end] == span.text
        for operation in proposal.operations
        for span in operation.evidence_spans
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _validation_error_summary(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "loc": ".".join(str(part) for part in error.get("loc", ())) or "root",
            "type": str(error.get("type") or "validation_error"),
        }
        for error in exc.errors()
    ]
