"""Frozen-context structured Narrative Agent with a deterministic fallback."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from autoad_researcher.reporting.default_narrative import build_default_narrative
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeSectionsV1
from autoad_researcher.ui.chat_client import call_research_chat

NARRATIVE_AGENT_PROFILE = "structured-chat-v1"


@dataclass(frozen=True)
class NarrativeGeneration:
    narrative: NarrativeSectionsV1
    mode: str
    model: str | None
    fallback_reason: str | None = None
    profile: dict[str, str] | None = None


def generate_narrative(*, facts: ExperimentReportFactsV1, evidence: EvidenceIndex, profile: dict[str, str] | None = None) -> NarrativeGeneration:
    """Generate only structured prose bound to a frozen Facts/Evidence context."""

    selected = profile or _configured_profile()
    config = _configured_provider(selected)
    if config is None:
        return NarrativeGeneration(
            narrative=build_default_narrative(facts),
            mode="deterministic_fallback",
            model=None,
            fallback_reason="report narrative provider is not configured",
            profile=selected,
        )
    api_key, provider_url, model = config
    result = call_research_chat(
        api_key,
        provider_url,
        _messages(facts, evidence),
        model=model,
        timeout_s=90,
        response_format_json=True,
        temperature=0,
        max_tokens=3200,
    )
    reply = result.get("reply")
    if result.get("error") or not isinstance(reply, str):
        return _fallback(facts, model, selected, "provider call did not return a structured response")
    try:
        narrative = NarrativeSectionsV1.model_validate(json.loads(reply))
    except (json.JSONDecodeError, ValueError):
        return _fallback(facts, model, selected, "provider response did not match NarrativeSectionsV1")
    return NarrativeGeneration(narrative=narrative, mode="model", model=model, profile=selected)


def _configured_profile() -> dict[str, str]:
    api_key = os.environ.get("AUTOAD_REPORT_API_KEY", "").strip()
    provider_url = os.environ.get("AUTOAD_REPORT_BASE_URL", "").strip()
    model = os.environ.get("AUTOAD_REPORT_MODEL", "").strip()
    return {
        "profile_version": "v1",
        "mode": "model" if api_key and provider_url and model else "deterministic_fallback",
        "model": model if api_key and provider_url and model else "",
        "provider_base_url": provider_url.rstrip("/") if api_key and provider_url and model else "",
        "prompt_sha256": "runtime-legacy-profile",
    }


def _configured_provider(profile: dict[str, str]) -> tuple[str, str, str] | None:
    api_key = os.environ.get("AUTOAD_REPORT_API_KEY", "").strip()
    if profile.get("mode") != "model" or not api_key:
        return None
    provider_url = profile.get("provider_base_url", "").strip()
    model = profile.get("model", "").strip()
    if not provider_url or not model:
        return None
    return api_key, provider_url, model


def _messages(facts: ExperimentReportFactsV1, evidence: EvidenceIndex) -> list[dict[str, str]]:
    context = {
        "facts": facts.model_dump(mode="json"),
        "evidence": [item.model_dump(mode="json") for item in evidence.entries],
    }
    return [
        {
            "role": "system",
            "content": (
                "You are AutoAD's Narrative Agent. Use only the supplied frozen Facts and Evidence. "
                "Return JSON only, matching NarrativeSectionsV1. It must contain summary, interpretation, "
                "limitations, and next_steps exactly once. Every factual claim must include fact_refs and "
                "evidence_ids from the supplied index. Do not state improvement for NON_COMPARABLE attempts. "
                "Do not create actions, metrics, attempts, or evidence IDs."
            ),
        },
        {"role": "user", "content": json.dumps(context, ensure_ascii=False, sort_keys=True)},
    ]


def _fallback(facts: ExperimentReportFactsV1, model: str, profile: dict[str, str], reason: str) -> NarrativeGeneration:
    return NarrativeGeneration(
        narrative=build_default_narrative(facts),
        mode="deterministic_fallback",
        model=model,
        fallback_reason=reason,
        profile=profile,
    )
