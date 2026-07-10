"""V2 Orchestrator — the single entry point for Research Assistant.

Usage:
    result = ResearchOrchestratorV2.handle(run_dir, user_input="...")
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.source_action_planner import SourceActionPlan, plan_source_actions
from autoad_researcher.assistant.v2.event_service import append_event, append_typed_event
from autoad_researcher.assistant.v2.source_service import register_source_intake
from autoad_researcher.assistant.v2.job_service import append_pipeline_job
from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.intent_contract import (
    ResearchIntentContract,
    build_contract_from_context,
    format_contract_for_user,
    load_contract_draft,
    merge_contract_draft,
    save_confirmed_contract,
    save_contract_draft,
)
from autoad_researcher.assistant.v2.reply_planner import plan_reply
from autoad_researcher.assistant.v2.turn_gate import decide_turn_gate_with_llm
from autoad_researcher.ui.sources import load_source_registry, remove_source


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
        on_reply_delta: Callable[[str], None] | None = None,
    ) -> OrchestratorResult:
        user_input = user_input.strip()
        if not user_input:
            return OrchestratorResult(reply="请输入问题。", reply_kind="answer")

        created_sources: list[dict[str, Any]] = []
        created_jobs: list[dict[str, Any]] = []
        ctx = build_llm_context(run_dir, transcript_tail=transcript_tail)
        existing_draft = load_contract_draft(run_dir)

        removed_source = _maybe_remove_latest_source(run_dir, user_input)
        if removed_source is not None:
            ctx = build_llm_context(run_dir, transcript_tail=transcript_tail)
            source = removed_source.get("source", {})
            label = source.get("user_label") or source.get("stored_path") or removed_source.get("source_id")
            return OrchestratorResult(
                reply=f"已删除刚才误上传的资料：{label}。右侧 Sources / Evidence 已同步刷新。",
                reply_kind="source_deleted",
                evidence_used=ctx.get("usable_evidence", []),
                answerability=ctx.get("answerability", {}),
                next_actions=_suggest_next_actions(ctx, "source_deleted"),
                intent_contract=existing_draft.model_dump(mode="json") if existing_draft is not None else {},
                intent_contract_confirmed=False,
            )

        source_plan = plan_source_actions(
            run_dir=run_dir,
            user_input=user_input,
            attachments=attachments,
            transcript_tail=transcript_tail,
            existing_contract_draft=(
                existing_draft.model_dump(mode="json") if existing_draft is not None else None
            ),
            source_registry=_source_registry_sources(run_dir),
            pending_jobs=ctx.get("pending_jobs", []) or [],
            api_key=api_key,
            provider_url=provider_url,
        )
        _append_source_action_decided_event(run_dir, source_plan)
        created_sources, created_jobs = _execute_source_action_plan(run_dir, user_input, source_plan)
        if created_sources or created_jobs:
            ctx = build_llm_context(run_dir, transcript_tail=transcript_tail)
        ctx["source_action_plan"] = source_plan.model_dump(mode="json")

        clarification = _source_plan_clarification(source_plan)
        if clarification and not created_sources and not created_jobs:
            return OrchestratorResult(
                reply=clarification,
                reply_kind="source_clarification",
                evidence_used=ctx.get("usable_evidence", []),
                answerability=ctx.get("answerability", {}),
                next_actions=_suggest_next_actions(ctx, "source_clarification"),
                intent_contract=existing_draft.model_dump(mode="json") if existing_draft is not None else {},
                intent_contract_confirmed=False,
            )

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
            run_dir=run_dir,
        )
        _append_turn_gate_decided_event(run_dir, turn_decision)
        ctx["turn_gate_decision"] = turn_decision.model_dump(mode="json")

        if created_sources or created_jobs:
            if not turn_decision.contract_update_allowed or not turn_decision.need_discovery_allowed:
                reply_kind, reply = _source_intake_reply(created_sources, created_jobs)
                return OrchestratorResult(
                    reply=reply,
                    reply_kind=reply_kind,
                    created_sources=created_sources,
                    created_jobs=created_jobs,
                    evidence_used=ctx.get("usable_evidence", []),
                    answerability=ctx.get("answerability", {}),
                    next_actions=_suggest_next_actions(ctx, reply_kind),
                    intent_contract=existing_draft.model_dump(mode="json") if existing_draft is not None else {},
                    intent_contract_confirmed=False,
                )

        if turn_decision.contract_action == "confirm_contract":
            contract = existing_draft
            draft_persisted = contract is not None
            if contract is None:
                recovered_update = build_contract_from_context(
                    run_dir=run_dir,
                    user_input=user_input,
                    llm_context=ctx,
                    transcript_tail=transcript_tail,
                    existing_contract_draft=None,
                    api_key=api_key,
                    provider_url=provider_url,
                )
                contract = merge_contract_draft(None, recovered_update)
                if _has_contract_content(contract):
                    save_contract_draft(run_dir, contract)
                    draft_persisted = True
                else:
                    contract = None
            if contract is not None:
                ctx["research_intent_contract"] = contract.model_dump(mode="json")
            if contract is not None and contract.ready_for_plan:
                if not draft_persisted:
                    save_contract_draft(run_dir, contract)
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
            reply_kind, reply = plan_reply(
                ctx,
                user_input,
                api_key=api_key,
                provider_url=provider_url,
                on_delta=on_reply_delta,
                run_dir=run_dir,
            )
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
            reply_kind, reply = plan_reply(
                ctx,
                user_input,
                api_key=api_key,
                provider_url=provider_url,
                on_delta=on_reply_delta,
                run_dir=run_dir,
            )
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
        if turn_decision.save_draft_allowed or contract.ready_for_plan:
            save_contract_draft(run_dir, contract)
        ctx["research_intent_contract"] = contract.model_dump(mode="json")

        # Source intake turn: brief status, don't reprint contract
        if created_sources or created_jobs:
            reply_kind, reply = _source_intake_reply(created_sources, created_jobs)
        elif contract.ready_for_plan:
            _append_contract_confirmation_requested_event(run_dir, contract)
            reply_kind, reply = "intent_contract_confirmation", format_contract_for_user(contract)
        else:
            reply_kind, reply = plan_reply(
                ctx,
                user_input,
                api_key=api_key,
                provider_url=provider_url,
                on_delta=on_reply_delta,
                run_dir=run_dir,
            )

        return OrchestratorResult(
            reply=reply,
            reply_kind=reply_kind,
            created_sources=created_sources,
            created_jobs=created_jobs,
            evidence_used=ctx.get("usable_evidence", []),
            answerability=ctx.get("answerability", {}),
            next_actions=_suggest_next_actions(ctx, reply_kind),
            intent_contract=contract.model_dump(mode="json"),
            intent_contract_confirmed=False,
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


def _has_contract_content(contract: ResearchIntentContract) -> bool:
    return any((
        contract.research_goal,
        contract.baseline,
        contract.dataset,
        contract.primary_metrics,
        contract.success_criteria,
    ))


def _append_source_action_decided_event(run_dir: Path, plan: SourceActionPlan) -> None:
    actions = plan.actions
    append_typed_event(run_dir, "planner.source_action.decided", {
        "action_count": len(actions),
        "action_types": _unique_strings([action.action_type for action in actions]),
        "source_kinds": _unique_strings([action.source_kind for action in actions if action.source_kind]),
        "requires_confirmation_count": sum(1 for action in actions if action.requires_confirmation),
        "confidence": plan.confidence,
        "has_user_visible_summary": bool(plan.user_visible_summary),
    })


def _append_turn_gate_decided_event(run_dir: Path, decision) -> None:
    append_typed_event(run_dir, "planner.turn_gate.decided", {
        "turn_type": decision.turn_type,
        "contract_action": decision.contract_action,
        "contract_update_allowed": decision.contract_update_allowed,
        "need_discovery_allowed": decision.need_discovery_allowed,
        "save_draft_allowed": decision.save_draft_allowed,
        "confidence": decision.confidence,
    })


def _append_contract_confirmation_requested_event(run_dir: Path, contract) -> None:
    append_typed_event(run_dir, "contract.confirmation.requested", {
        "ready_for_plan": contract.ready_for_plan,
        "ready_for_repo_analysis": contract.ready_for_repo_analysis,
        "ready_for_experiment_agents": contract.ready_for_experiment_agents,
        "missing_required_fields": list(contract.missing_required_fields),
        "primary_metrics_count": len(contract.primary_metrics),
        "has_baseline_repo": bool(contract.baseline_repo),
    })


def _unique_strings(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _maybe_remove_latest_source(run_dir: Path, user_input: str) -> dict[str, Any] | None:
    text = user_input.strip()
    if not _is_source_removal_request(text):
        return None
    sources = _source_registry_sources(run_dir)
    if not sources:
        return None
    latest = max(sources, key=lambda item: str(item.get("created_at") or ""))
    source_id = str(latest.get("source_id") or "")
    if not source_id:
        return None
    removed = remove_source(run_dir, source_id, reason="user_rejected_latest_upload")
    if removed is None:
        return None
    append_event(run_dir, "source.deleted", {"source_id": source_id})
    append_event(run_dir, "evidence.updated", {"source_id": source_id})
    append_event(run_dir, "toast.success", {"message": "已删除误上传资料"})
    return removed


def _is_source_removal_request(text: str) -> bool:
    lowered = text.lower()
    has_wrong_signal = any(token in lowered for token in ("上传错", "传错", "不是我们要的", "不是我要的", "不相关", "删掉", "删除"))
    has_source_signal = any(token in lowered for token in ("资料", "文件", "上传", "source", "evidence", "这个", "刚才"))
    return has_wrong_signal and has_source_signal


def _execute_source_action_plan(
    run_dir: Path,
    user_input: str,
    source_plan: SourceActionPlan,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    created_sources: list[dict[str, Any]] = []
    created_jobs: list[dict[str, Any]] = []
    registered_urls: dict[str, dict[str, Any]] = {}

    for action in source_plan.actions:
        if action.requires_confirmation:
            continue
        if action.action_type in {"answer_only", "ask_clarification", "repo_summarize"}:
            continue

        if action.action_type == "web_search":
            query = action.query or action.target or user_input
            job = append_pipeline_job(
                run_dir,
                source_id="search",
                job_type="web_search",
                evidence_role="candidate_source_only",
                payload={
                    "query": query,
                    "planner_action": action.model_dump(mode="json"),
                },
            )
            created_jobs.append(job)
            continue

        if action.action_type == "github_discovery":
            query = action.query or action.target or user_input
            job = append_pipeline_job(
                run_dir,
                source_id="search",
                job_type="web_search",
                evidence_role="candidate_source_only",
                payload={
                    "query": query,
                    "intent": "github_discovery",
                    "planner_action": action.model_dump(mode="json"),
                },
            )
            created_jobs.append(job)
            continue

        if action.action_type in {"register_webpage", "register_github_repo", "git_clone"}:
            if not action.source_url:
                continue
            source_kind = "github_repo" if action.action_type in {"register_github_repo", "git_clone"} else "webpage"
            src = registered_urls.get(action.source_url)
            if src is None:
                src = register_source_intake(
                    run_dir,
                    user_input=user_input,
                    source_kind=source_kind,
                    source_url=action.source_url,
                )
                registered_urls[action.source_url] = src
                created_sources.append(src)
            source_id = str(src.get("source_id", ""))
            if source_kind == "github_repo":
                clone_job = append_pipeline_job(
                    run_dir,
                    source_id=source_id,
                    job_type="git_clone",
                    evidence_role="candidate_source_only",
                    payload={"planner_action": action.model_dump(mode="json")},
                )
                created_jobs.append(clone_job)
                summarize_job = append_pipeline_job(
                    run_dir,
                    source_id=source_id,
                    job_type="repo_summarize",
                    evidence_role="repo_acquired",
                    payload={"depends_on": clone_job.get("job_id"), "planner_action": action.model_dump(mode="json")},
                )
                created_jobs.append(summarize_job)
            else:
                fetch_job = append_pipeline_job(
                    run_dir,
                    source_id=source_id,
                    job_type="web_fetch",
                    evidence_role="source_acquired_unparsed",
                    payload={"planner_action": action.model_dump(mode="json")},
                )
                created_jobs.append(fetch_job)
                markdown_job = append_pipeline_job(
                    run_dir,
                    source_id=source_id,
                    job_type="web_markitdown",
                    evidence_role="parsed_web_evidence",
                    payload={"depends_on": fetch_job.get("job_id"), "planner_action": action.model_dump(mode="json")},
                )
                created_jobs.append(markdown_job)

    return created_sources, created_jobs


def _source_registry_sources(run_dir: Path) -> list[dict[str, Any]]:
    registry = load_source_registry(run_dir)
    sources = registry.get("sources", [])
    return [item for item in sources if isinstance(item, dict)]


def _source_plan_clarification(source_plan: SourceActionPlan) -> str:
    if not source_plan.user_visible_summary:
        return ""
    if any(action.action_type == "ask_clarification" for action in source_plan.actions):
        return source_plan.user_visible_summary
    return ""


def _source_intake_reply(
    created_sources: list[dict[str, Any]],
    created_jobs: list[dict[str, Any]],
) -> tuple[str, str]:
    """Brief status reply for source intake — never reprints contract."""
    job_types = [j.get("job_type", "") for j in created_jobs]
    if "git_clone" in job_types or "repo_analyze" in job_types:
        return "source_intake", (
            "已登记代码仓库，后台开始 clone 和 repo analysis。"
            "完成后右侧 Evidence 会出现仓库摘要。"
        )
    if "web_search" in job_types:
        return "source_intake", (
            "已登记搜索任务，后台只会整理候选资料。"
            "候选来源需要 fetch/parse 后才会进入右侧 Evidence。"
        )
    return "source_intake", (
        "已登记资料链接，后台开始 fetch/parse。"
        "完成后右侧 Evidence 会出现可用摘要。"
    )
