"""Frozen-context structured Narrative Agent with a deterministic fallback."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from autoad_researcher.assistant.model_routing import ModelRoute, select_model_route
from autoad_researcher.reporting.default_narrative import build_default_narrative
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeSectionsV1
from autoad_researcher.ui.chat_client import call_research_chat

NARRATIVE_AGENT_PROFILE = "structured-chat-v1"


class NarrativeGenerationError(RuntimeError):
    """A selected model profile could not produce a publishable Narrative."""


@dataclass(frozen=True)
class NarrativeGeneration:
    narrative: NarrativeSectionsV1
    mode: str
    model: str | None
    fallback_reason: str | None = None
    profile: dict[str, Any] | None = None


def generate_narrative(*, facts: ExperimentReportFactsV1, evidence: EvidenceIndex, profile: dict[str, Any] | None = None) -> NarrativeGeneration:
    """Generate only structured prose bound to a frozen Facts/Evidence context."""

    selected = profile or _configured_profile()
    if selected.get("mode") == "deterministic_fallback":
        return NarrativeGeneration(
            narrative=build_default_narrative(facts),
            mode="deterministic_fallback",
            model=None,
            fallback_reason="report narrative provider was not configured at report creation",
            profile=selected,
        )
    config = _configured_provider(selected)
    if config is None:
        raise NarrativeGenerationError("selected report Narrative model profile is unavailable at execution time")
    api_key, provider_url, model, route = config
    messages = _messages(facts, evidence)
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model,
        timeout_s=90,
        response_format_json=True,
        temperature=0,
        thinking_type=route.thinking_type,
        reasoning_effort=route.reasoning_effort,
    )
    reply = result.get("reply")
    if result.get("error") or not isinstance(reply, str):
        raise NarrativeGenerationError("report Narrative provider did not return a structured response")
    finish_reason = result.get("runtime", {}).get("finish_reason") if isinstance(result.get("runtime"), dict) else None
    narrative, diagnostic = _parse_narrative(reply, finish_reason=finish_reason)
    if narrative is None:
        repair = call_research_chat(
            api_key,
            provider_url,
            [
                *messages,
                {"role": "assistant", "content": reply[:16000]},
                {"role": "user", "content": _repair_prompt(diagnostic or "structured output validation failed")},
            ],
            model=model,
            timeout_s=90,
            response_format_json=True,
            temperature=0,
            thinking_type=route.thinking_type,
            reasoning_effort=route.reasoning_effort,
        )
        repaired_reply = repair.get("reply")
        if repair.get("error") or not isinstance(repaired_reply, str):
            raise NarrativeGenerationError(
                "report Narrative provider response did not match NarrativeSectionsV1 "
                f"({diagnostic or 'initial validation failed'}; repair provider failed)"
            )
        repair_finish_reason = repair.get("runtime", {}).get("finish_reason") if isinstance(repair.get("runtime"), dict) else None
        narrative, repair_diagnostic = _parse_narrative(repaired_reply, finish_reason=repair_finish_reason)
        if narrative is None:
            progress = "; no_progress" if repaired_reply == reply else ""
            raise NarrativeGenerationError(
                "report Narrative provider response did not match NarrativeSectionsV1 "
                f"({repair_diagnostic or diagnostic or 'repair validation failed'}{progress})"
            )
    return NarrativeGeneration(narrative=narrative, mode="model", model=model, profile=selected)


def _configured_profile() -> dict[str, Any]:
    route = select_model_route("report", os.environ.get("AUTOAD_REPORT_MODEL", "").strip() or None)
    api_key = os.environ.get("AUTOAD_REPORT_API_KEY", "").strip()
    provider_url = os.environ.get("AUTOAD_REPORT_BASE_URL", "").strip()
    return {
        "profile_version": "v1",
        "mode": "model" if api_key and provider_url else "deterministic_fallback",
        "model": route.model_id,
        "model_id": route.model_id,
        "role": route.role,
        "thinking_type": route.thinking_type,
        "reasoning_effort": route.reasoning_effort or "",
        "context_window": route.context_window,
        "max_output_capability": route.max_output_capability,
        "routing_schema_version": route.routing_schema_version,
        "model_route": route.snapshot(),
        "provider_base_url": provider_url.rstrip("/") if api_key and provider_url else "",
        "prompt_sha256": "runtime-legacy-profile",
    }


def _configured_provider(profile: dict[str, Any]) -> tuple[str, str, str, ModelRoute] | None:
    api_key = os.environ.get("AUTOAD_REPORT_API_KEY", "").strip()
    if profile.get("mode") != "model" or not api_key:
        return None
    provider_url = profile.get("provider_base_url", "").strip()
    model = str(profile.get("model_id") or profile.get("model") or "").strip()
    if not provider_url or not model:
        return None
    route = select_model_route("report", model)
    return api_key, provider_url, route.model_id, route


def _messages(facts: ExperimentReportFactsV1, evidence: EvidenceIndex) -> list[dict[str, str]]:
    context = {
        "facts": facts.model_dump(mode="json"),
        "evidence": [item.model_dump(mode="json") for item in evidence.entries],
        "fact_evidence_bindings": _fact_evidence_bindings(evidence),
    }
    return [
        {
            "role": "system",
            "content": narrative_system_prompt(),
        },
        {"role": "user", "content": json.dumps(context, ensure_ascii=False, sort_keys=True)},
    ]


def narrative_system_prompt() -> str:
    """The exact static system prompt committed into the report recipe."""

    schema = json.dumps(NarrativeSectionsV1.model_json_schema(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        "You are AutoAD's Narrative Agent. Use only the supplied frozen Facts and Evidence. "
        "Return one JSON object only, matching NarrativeSectionsV1 exactly. It must contain schema_version=2, "
        "sections, and claims. The sections array must contain summary, interpretation, limitations, and "
        "next_steps exactly once. Every section must contain at least one paragraph. Every factual claim must include fact_refs and "
        "evidence_ids from the supplied index. Do not state improvement for NON_COMPARABLE attempts. "
        "Copy every claim fact_refs value exactly from the fact_ref values in fact_evidence_bindings; do not invent shorthand, top-level field names, or paths without a registered Evidence binding. "
        "For each claim, choose evidence_ids from the matching binding entries for its fact_refs. "
        "Every claim object must bind at least one Fact and one matching Evidence ID; never emit a claim with empty fact_refs or evidence_ids. "
        "Only create claims for paragraphs that make a factual, interpretive, limiting, or recommendation assertion grounded in the supplied context; do not create empty claims for background or next_steps paragraphs. "
        "Do not create actions, metrics, attempts, or evidence IDs. Do not add fields outside this schema. "
        "The authoritative JSON Schema is: "
        f"{schema} "
        "The supplied frozen context is the only source of truth."
    )


def _fact_evidence_bindings(evidence: EvidenceIndex) -> list[dict[str, Any]]:
    bindings: dict[str, set[str]] = {}
    for entry in evidence.entries:
        for fact_ref in entry.fact_refs:
            bindings.setdefault(fact_ref, set()).add(entry.evidence_id)
    return [
        {"fact_ref": fact_ref, "evidence_ids": sorted(evidence_ids)}
        for fact_ref, evidence_ids in sorted(bindings.items())
        if evidence_ids
    ]


def _repair_prompt(diagnostic: str) -> str:
    return (
        "Your previous Narrative JSON did not pass the authoritative schema. Return only a corrected JSON object; "
        "do not explain the repair and do not add fields. Validation diagnostic: "
        f"{diagnostic}"
    )


def _parse_narrative(reply: str, *, finish_reason: str | None) -> tuple[NarrativeSectionsV1 | None, str | None]:
    try:
        raw = json.loads(reply)
    except json.JSONDecodeError as exc:
        suffix = f"; finish_reason={finish_reason}" if finish_reason else ""
        return None, f"invalid_json at line {exc.lineno} column {exc.colno}{suffix}"
    try:
        return NarrativeSectionsV1.model_validate(raw), None
    except ValidationError as exc:
        details: list[str] = []
        for item in exc.errors()[:8]:
            location = ".".join(str(part) for part in item.get("loc", ())) or "$"
            details.append(f"{location}: {item.get('type', 'validation_error')}")
        suffix = f"; finish_reason={finish_reason}" if finish_reason else ""
        return None, f"schema_validation ({'; '.join(details)}){suffix}"
    except (TypeError, ValueError):
        return None, "schema_validation (root object has an invalid shape)"
