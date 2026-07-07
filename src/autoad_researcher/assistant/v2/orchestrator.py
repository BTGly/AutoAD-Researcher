"""V2 Orchestrator — the single entry point for Research Assistant.

Usage:
    result = ResearchOrchestratorV2.handle(run_dir, user_input="...")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.source_service import classify_input, register_source_intake
from autoad_researcher.assistant.v2.job_service import append_pipeline_job
from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.intent_contract import (
    build_contract_from_context,
    format_contract_for_user,
    load_contract_draft,
    merge_contract_draft,
    save_confirmed_contract,
    save_contract_draft,
)
from autoad_researcher.assistant.v2.reply_planner import plan_reply
from autoad_researcher.assistant.v2.turn_gate import decide_turn_gate_with_llm


@dataclass
class OrchestratorResult:
    reply: str = ""
    reply_kind: str = "answer"
    created_sources: list[dict[str, Any]] = field(default_factory=list)
    created_jobs: list[dict[str, Any]] = field(default_factory=list)
    evidence_used: list[dict[str, Any]] = field(default_factory=list)
    answerability: dict[str, Any] = field(default_factory=dict)
    next_actions: list[str] = field(default_factory=list)
    intent_contract: dict[str, Any] = field(default_factory=dict)
    intent_contract_confirmed: bool = False


class ResearchOrchestratorV2:
    """Single entry point for all research assistant interactions."""

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
        created_jobs: list[dict[str, Any]] = []

        if intent in ("paper_pdf", "webpage", "github_repo"):
            src = register_source_intake(run_dir, user_input=user_input, source_kind=intent)
            created_sources.append(src)
            source_id = src.get("source_id", "")
            evidence_role = "source_acquired_unparsed"

            if intent == "github_repo":
                job = append_pipeline_job(run_dir, source_id=source_id, job_type="git_clone", evidence_role="candidate_source_only")
                created_jobs.append(job)
                job2 = append_pipeline_job(run_dir, source_id=source_id, job_type="repo_analyze", evidence_role="candidate_source_only")
                created_jobs.append(job2)
            elif intent == "paper_pdf":
                job = append_pipeline_job(run_dir, source_id=source_id, job_type="paper_parse", evidence_role=evidence_role)
                created_jobs.append(job)
            else:
                job = append_pipeline_job(run_dir, source_id=source_id, job_type="web_fetch", evidence_role=evidence_role)
                created_jobs.append(job)
                job2 = append_pipeline_job(run_dir, source_id=source_id, job_type="paper_parse", evidence_role=evidence_role)
                created_jobs.append(job2)

        if intent == "web_search":
            job = append_pipeline_job(run_dir, source_id="search", job_type="web_search", evidence_role="candidate_source_only")
            created_jobs.append(job)

        ctx = build_llm_context(run_dir, transcript_tail=transcript_tail)
        existing_draft = load_contract_draft(run_dir)
        turn_decision = decide_turn_gate_with_llm(
            user_input=user_input,
            transcript_tail=transcript_tail,
            existing_contract_draft=(
                existing_draft.model_dump(mode="json") if existing_draft is not None else None
            ),
            created_sources=created_sources,
            created_jobs=created_jobs,
            answerability=ctx.get("answerability", {}) or {},
            api_key=api_key,
            provider_url=provider_url,
        )
        ctx["turn_gate_decision"] = turn_decision.model_dump(mode="json")

        if turn_decision.contract_action == "confirm_contract":
            contract = existing_draft
            if contract is not None:
                ctx["research_intent_contract"] = contract.model_dump(mode="json")
            if contract is not None and contract.ready_for_plan:
                save_confirmed_contract(run_dir, contract)
                return OrchestratorResult(
                    reply=(
                        "已确认 ResearchIntentContract，并写入 `research_intent_contract.json`。"
                        "不会自动 patch 或运行实验；后续 agents 将以这个合同作为输入。"
                    ),
                    reply_kind="intent_contract_confirmed",
                    created_sources=created_sources,
                    created_jobs=created_jobs,
                    evidence_used=ctx.get("usable_evidence", []),
                    answerability=ctx.get("answerability", {}),
                    next_actions=_suggest_next_actions(ctx, "intent_contract_confirmed"),
                    intent_contract=contract.model_dump(mode="json"),
                    intent_contract_confirmed=True,
                )
            reply_kind, reply = plan_reply(ctx, user_input, api_key=api_key, provider_url=provider_url)
            return OrchestratorResult(
                reply=reply,
                reply_kind=reply_kind,
                created_sources=created_sources,
                created_jobs=created_jobs,
                evidence_used=ctx.get("usable_evidence", []),
                answerability=ctx.get("answerability", {}),
                next_actions=_suggest_next_actions(ctx, reply_kind),
                intent_contract=contract.model_dump(mode="json") if contract is not None else {},
                intent_contract_confirmed=False,
            )

        if not turn_decision.contract_update_allowed or not turn_decision.need_discovery_allowed:
            contract = existing_draft
            if contract is not None:
                ctx["research_intent_contract"] = contract.model_dump(mode="json")
            reply_kind, reply = plan_reply(ctx, user_input, api_key=api_key, provider_url=provider_url)
            return OrchestratorResult(
                reply=reply,
                reply_kind=reply_kind,
                created_sources=created_sources,
                created_jobs=created_jobs,
                evidence_used=ctx.get("usable_evidence", []),
                answerability=ctx.get("answerability", {}),
                next_actions=_suggest_next_actions(ctx, reply_kind),
                intent_contract=contract.model_dump(mode="json") if contract is not None else {},
                intent_contract_confirmed=False,
            )

        contract_update = build_contract_from_context(
            run_dir=run_dir,
            user_input=user_input,
            llm_context=ctx,
            transcript_tail=transcript_tail,
            existing_contract_draft=existing_draft,
            api_key=api_key,
            provider_url=provider_url,
        )
        contract = merge_contract_draft(existing_draft, contract_update)
        if turn_decision.save_draft_allowed:
            save_contract_draft(run_dir, contract)
        ctx["research_intent_contract"] = contract.model_dump(mode="json")

        contract_confirmed = False
        if contract.ready_for_plan:
            reply_kind, reply = "intent_contract_confirmation", format_contract_for_user(contract)
        else:
            reply_kind, reply = plan_reply(ctx, user_input, api_key=api_key, provider_url=provider_url)

        return OrchestratorResult(
            reply=reply,
            reply_kind=reply_kind,
            created_sources=created_sources,
            created_jobs=created_jobs,
            evidence_used=ctx.get("usable_evidence", []),
            answerability=ctx.get("answerability", {}),
            next_actions=_suggest_next_actions(ctx, reply_kind),
            intent_contract=contract.model_dump(mode="json"),
            intent_contract_confirmed=contract_confirmed,
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
