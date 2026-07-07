"""V2 Orchestrator — the single entry point for Research Assistant.

Usage:
    result = ResearchOrchestratorV2.handle(run_dir, user_input="...")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.source_service import classify_input, register_source_intake
from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.reply_planner import plan_reply


@dataclass
class OrchestratorResult:
    reply: str = ""
    reply_kind: str = "answer"
    created_sources: list[dict[str, Any]] = field(default_factory=list)
    evidence_used: list[dict[str, Any]] = field(default_factory=list)
    answerability: dict[str, Any] = field(default_factory=dict)
    next_actions: list[str] = field(default_factory=list)


class ResearchOrchestratorV2:
    """Single entry point for all research assistant interactions.

    Determines intent, registers sources, checks evidence, and returns
    a structured reply based on answerability — not ResponseMode templates.
    """

    @classmethod
    def handle(
        cls,
        run_dir: Path,
        *,
        user_input: str,
        attachments: list[str] | None = None,
        transcript_tail: list[dict[str, Any]] | None = None,
        api_key: str = "",
        provider_url: str = "",
    ) -> OrchestratorResult:
        user_input = user_input.strip()
        if not user_input:
            return OrchestratorResult(reply="请输入问题。", reply_kind="answer")

        intent = classify_input(user_input, attachments)
        created_sources: list[dict[str, Any]] = []

        if intent in ("paper_pdf", "webpage", "github_repo"):
            created_sources.append(register_source_intake(
                run_dir, user_input=user_input, source_kind=intent,
            ))

        ctx = build_llm_context(run_dir, transcript_tail=transcript_tail)

        reply_kind, reply = plan_reply(ctx, user_input, api_key=api_key, provider_url=provider_url)

        return OrchestratorResult(
            reply=reply,
            reply_kind=reply_kind,
            created_sources=created_sources,
            evidence_used=ctx.get("usable_evidence", []),
            answerability=ctx.get("answerability", {}),
            next_actions=_suggest_next_actions(ctx, reply_kind),
        )


def _suggest_next_actions(ctx: dict, reply_kind: str) -> list[str]:
    actions = []
    blocking = ctx.get("answerability", {}).get("blocking_next_step")
    if blocking == "intake":
        actions.append("upload a PDF or paste a URL")
    elif blocking == "fetch":
        actions.append("trigger web_fetch on candidate sources")
    elif blocking == "parse":
        actions.append("parse registered sources")
    return actions
