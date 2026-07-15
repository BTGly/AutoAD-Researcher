"""LLMContext builder for V2. Combines confirmed chat facts with evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.assistant.chat_facts import extract_confirmed_from_chat
from autoad_researcher.assistant.v2.evidence_service import (
    load_candidate_sources,
    load_unusable_parsed_sources,
    load_unparsed_sources,
    load_usable_evidence,
)
from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs

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
    unusable_parsed = load_unusable_parsed_sources(run_dir)
    jobs = load_pipeline_jobs(run_dir)
    pending_jobs = [
        _job_context(j)
        for j in jobs
        if j.get("status") in {"queued", "running"}
    ]
    failed_jobs = [
        _job_context(j)
        for j in jobs
        if j.get("status") == "failed"
    ]

    answer_entry_types = {
        "paper_summary",
        "paper_reading_summary",
        "uploaded_text",
        "web_markdown",
        "paper_markdown_fallback",
        "repo_summary",
    }
    can_answer = any(e.get("evidence_type") in answer_entry_types and e.get("summary") for e in usable)
    limitations = []
    blocking_step = None

    if not can_answer:
        if unparsed:
            blocking_step = "parse"
            limitations.append(f"{len(unparsed)} source(s) registered but not parsed")
            if pending_jobs:
                limitations.append(f"{len(pending_jobs)} parse/acquisition job(s) queued or running")
        elif unusable_parsed:
            blocking_step = "parse_quality"
            limitations.append(f"{len(unusable_parsed)} parsed source(s) did not produce usable text evidence")
        elif candidates:
            blocking_step = "fetch"
            limitations.append("candidate sources not fetched/parsed")
        else:
            blocking_step = "intake"
            limitations.append("no sources registered")

    return {
        "recent_dialogue": _recent_dialogue(transcript_tail),
        "confirmed_from_user": confirmed,
        "usable_evidence": usable,
        "readable_summaries": [e.get("summary", "") for e in usable if e.get("summary")],
        "artifact_manifests": [
            {
                "source_id": e.get("source_id", ""),
                "artifact_path": e.get("artifact_path", ""),
                "summary": e.get("summary", ""),
                "raw": e.get("raw", {}),
            }
            for e in usable
            if e.get("evidence_type") == "paper_artifact_manifest"
        ],
        "paper_reading_summaries": [
            {
                "source_id": e.get("source_id", ""),
                "artifact_path": e.get("artifact_path", ""),
                "summary": e.get("summary", ""),
                "raw": e.get("raw", {}),
            }
            for e in usable
            if e.get("evidence_type") == "paper_reading_summary"
        ],
        "candidate_sources": [c.get("query", "") for c in candidates],
        "unparsed_sources": [s.get("source_id", "") for s in unparsed],
        "unusable_parsed_sources": unusable_parsed,
        "pending_jobs": pending_jobs,
        "failed_jobs": failed_jobs,
        "jobs": [_job_context(job) for job in jobs],
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
            "web_markitdown",
            "paper_parse_mineru",
            "paper_parse_markitdown",
            "git_clone",
            "repo_summarize",
            "source_registry",
            "research_context_draft",
            "freeze",
        ],
        "forbidden_actions": FORBIDDEN_ACTIONS,
    }


def _recent_dialogue(transcript_tail: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    dialogue: list[dict[str, str]] = []
    for entry in (transcript_tail or [])[-12:]:
        role = entry.get("role")
        content = entry.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            dialogue.append({"role": role, "content": content})
    return dialogue


def _job_context(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job.get("job_id", ""),
        "source_id": job.get("source_id", ""),
        "job_type": job.get("job_type", ""),
        "status": job.get("status", ""),
        "evidence_role": job.get("evidence_role", ""),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "error": job.get("error"),
    }
