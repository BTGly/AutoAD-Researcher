"""LLMContext builder for V2. Combines confirmed chat facts with evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.assistant.chat_facts import extract_confirmed_from_chat
from autoad_researcher.assistant.v2.evidence_service import (
    load_candidate_sources,
    load_unparsed_sources,
    load_usable_evidence,
)

FORBIDDEN_ACTIONS = [
    "patch_apply",
    "runner_execute",
    "benchmark_execute",
    "training",
    "evaluation",
    "git_commit",
    "submit_pr",
    "unrestricted_shell",
]


def build_llm_context(
    run_dir: Path,
    *,
    transcript_tail: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    confirmed = extract_confirmed_from_chat(transcript_tail or [])
    usable = load_usable_evidence(run_dir)
    candidates = load_candidate_sources(run_dir)
    unparsed = load_unparsed_sources(run_dir)

    can_answer = any(e.get("evidence_type") == "paper_summary" for e in usable) and any(
        e.get("summary") for e in usable if e.get("evidence_type") == "paper_summary"
    )
    limitations = []
    blocking_step = None

    if not can_answer:
        if unparsed:
            blocking_step = "parse"
            limitations.append(f"{len(unparsed)} source(s) registered but not parsed")
        elif candidates:
            blocking_step = "fetch"
            limitations.append("candidate sources not fetched/parsed")
        else:
            blocking_step = "intake"
            limitations.append("no sources registered")

    return {
        "confirmed_from_user": confirmed,
        "usable_evidence": usable,
        "readable_summaries": [e.get("summary", "") for e in usable if e.get("summary")],
        "candidate_sources": [c.get("query", "") for c in candidates],
        "unparsed_sources": [s.get("source_id", "") for s in unparsed],
        "failed_jobs": [],
        "answerability": {
            "can_answer": can_answer,
            "basis": [e.get("evidence_type", "") for e in usable],
            "limitations": limitations,
            "blocking_next_step": blocking_step,
        },
        "allowed_actions": [
            "web_search",
            "web_fetch",
            "paper_parse",
            "git_clone",
            "source_registry",
            "research_context_draft",
            "freeze",
        ],
        "forbidden_actions": FORBIDDEN_ACTIONS,
    }
