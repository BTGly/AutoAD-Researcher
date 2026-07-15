"""V2 Orchestrator — the single entry point for Research Assistant.

Usage:
    result = ResearchOrchestratorV2.handle(run_dir, user_input="...")
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.assistant.llm_runtime import with_conversation_deadline
from autoad_researcher.assistant.v2.conversation_router import (
    ConversationRouteDecision,
    deterministic_source_route,
    route_conversation_with_llm,
)
from autoad_researcher.assistant.v2.source_action_planner import (
    SourceActionPlan,
    explicit_source_input_is_url_only,
    plan_explicit_source_actions,
)
from autoad_researcher.assistant.v2.contract_confirmation_service import (
    ConfirmationConflict,
    apply_confirmation_action_proposal,
    load_active_contract_confirmation,
    load_pending_contract_confirmation,
    request_contract_confirmation,
)
from autoad_researcher.assistant.v2.event_service import append_event, append_typed_event, load_events_since
from autoad_researcher.assistant.v2.source_service import register_source_intake
from autoad_researcher.assistant.v2.job_service import append_pipeline_job
from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.intent_contract import (
    ResearchIntentContract,
    format_contract_for_user,
    load_confirmed_contract,
    load_contract_draft,
)
from autoad_researcher.assistant.v2.intent_mutation_service import interpret_and_apply_intent_mutation
from autoad_researcher.assistant.v2.mutation_protocol import MutationReceipt
from autoad_researcher.assistant.v2.reply_planner import plan_reply
from autoad_researcher.ui.sources import load_source_registry, remove_source
from autoad_researcher.task_workspace.task_profile import (
    apply_automatic_task_profile,
    build_automatic_task_profile,
    task_profile_needs_automatic_title,
)


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
    task_update: dict[str, Any] = field(default_factory=dict)
    mutation_receipt: dict[str, Any] = field(default_factory=dict)


class ResearchOrchestratorV2:
    """Single entry point for all research assistant interactions."""

    @classmethod
    @with_conversation_deadline
    def handle(
        cls,
        run_dir: Path,
        *,
        user_input: str,
        attachments: list[str] | None = None,
        transcript_tail: list[dict[str, Any]] | None = None,
        api_key: str = "",
        provider_url: str = "",
        model: str = "deepseek-v4-flash",
        on_reply_delta: Callable[[str], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_task_updated: Callable[[dict[str, Any]], None] | None = None,
    ) -> OrchestratorResult:
        user_input = user_input.strip()
        if not user_input:
            return OrchestratorResult(reply="请输入问题。", reply_kind="answer")

        created_sources = []
        created_jobs = []
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

        source_registry = _source_registry_sources(run_dir)
        explicit_source_plan = plan_explicit_source_actions(
            user_input=user_input,
            attachments=attachments,
            source_registry=source_registry,
        )
        structured_source_only = bool(
            explicit_source_plan is not None
            and not attachments
            and explicit_source_input_is_url_only(user_input)
        )
        if structured_source_only and explicit_source_plan is not None:
            route_decision = deterministic_source_route(explicit_source_plan)
            source_plan = explicit_source_plan
            created_sources, created_jobs = _execute_source_action_plan(
                run_dir, user_input, source_plan
            )
            _append_conversation_route_decided_event(run_dir, route_decision, mode="deterministic")
            reply_kind, reply = _source_intake_reply(created_sources, created_jobs)
            return OrchestratorResult(
                reply=reply,
                reply_kind=reply_kind,
                created_sources=created_sources,
                created_jobs=created_jobs,
                evidence_used=ctx.get("usable_evidence", []),
                answerability=ctx.get("answerability", {}),
                next_actions=_suggest_next_actions(ctx, reply_kind),
                intent_contract=(
                    existing_draft.model_dump(mode="json")
                    if existing_draft is not None else {}
                ),
            )

        if explicit_source_plan is not None:
            created_sources, created_jobs = _execute_source_action_plan(
                run_dir, user_input, explicit_source_plan
            )
            if created_sources or created_jobs:
                ctx = build_llm_context(run_dir, transcript_tail=transcript_tail)
                source_registry = _source_registry_sources(run_dir)

        route_decision = route_conversation_with_llm(
            run_dir=run_dir,
            user_input=user_input,
            transcript_tail=transcript_tail,
            existing_contract_draft=(
                existing_draft.model_dump(mode="json") if existing_draft is not None else None
            ),
            source_registry=source_registry,
            pending_jobs=ctx.get("pending_jobs", []) or [],
            created_sources=created_sources,
            created_jobs=created_jobs,
            answerability=ctx.get("answerability", {}) or {},
            api_key=api_key,
            provider_url=provider_url,
            model=model,
            deterministic_source_plan=explicit_source_plan,
        )
        _append_conversation_route_decided_event(
            run_dir,
            route_decision,
            mode="llm" if api_key else "fallback",
        )
        source_plan = route_decision.source_action_plan
        if explicit_source_plan is None:
            created_sources, created_jobs = _execute_source_action_plan(
                run_dir, user_input, source_plan
            )
            if created_sources or created_jobs:
                ctx = build_llm_context(run_dir, transcript_tail=transcript_tail)
        ctx["source_action_plan"] = source_plan.model_dump(mode="json")

        clarification = _source_plan_clarification(source_plan)
        if (
            clarification
            and not created_sources
            and not created_jobs
            and not route_decision.contract_mutation_request.requested
            and not route_decision.confirmation_request.requested
        ):
            return OrchestratorResult(
                reply=clarification,
                reply_kind="source_clarification",
                evidence_used=ctx.get("usable_evidence", []),
                answerability=ctx.get("answerability", {}),
                next_actions=_suggest_next_actions(ctx, "source_clarification"),
                intent_contract=existing_draft.model_dump(mode="json") if existing_draft is not None else {},
                intent_contract_confirmed=False,
            )

        turn_decision = route_decision.turn_gate
        ctx["turn_gate_decision"] = turn_decision.model_dump(mode="json")
        _emit_progress(
            on_progress,
            "正在核对实验约束……"
            if turn_decision.contract_action == "update_contract"
            else "正在准备回复……",
        )

        confirmed_contract = load_confirmed_contract(run_dir)
        if confirmed_contract is not None and (
            route_decision.contract_mutation_request.requested
            or route_decision.confirmation_request.requested
        ):
            return OrchestratorResult(
                reply=(
                    "当前任务的研究合同已经确认，不能在同一任务中覆盖。"
                    "如果要切换研究方向，请使用页面顶部的“新建任务”创建新任务；旧合同和实验准备状态会继续保留。"
                ),
                reply_kind="confirmed_contract_immutable",
                created_sources=created_sources,
                created_jobs=created_jobs,
                evidence_used=ctx.get("usable_evidence", []),
                answerability=ctx.get("answerability", {}),
                next_actions=[],
                intent_contract=confirmed_contract.model_dump(mode="json"),
                intent_contract_confirmed=True,
            )

        confirmation_request = route_decision.confirmation_request
        active_confirmation = load_active_contract_confirmation(run_dir)
        if (
            active_confirmation is not None
            and confirmation_request.requested
            and confirmation_request.action in {"suspend", "resume", "supersede"}
        ):
            evidence_quote = confirmation_request.full_turn_mutation_evidence
            try:
                confirmation_state = apply_confirmation_action_proposal(
                    run_dir,
                    action=confirmation_request.action,
                    confirmation_id=str(active_confirmation["confirmation_id"]),
                    draft_sha256=str(active_confirmation["draft_hash"]),
                    user_text=user_input,
                    evidence_quote=evidence_quote,
                )
            except ConfirmationConflict as exc:
                ctx["confirmation_action_error"] = exc.detail()
                contract = existing_draft
                if contract is not None:
                    ctx["research_intent_contract"] = contract.model_dump(mode="json")
                reply_kind, reply = plan_reply(
                    ctx,
                    user_input,
                    api_key=api_key,
                    provider_url=provider_url,
                    model=model,
                    on_delta=on_reply_delta,
                    run_dir=run_dir,
                )
                return OrchestratorResult(
                    reply=reply,
                    reply_kind=reply_kind,
                    evidence_used=ctx.get("usable_evidence", []),
                    answerability=ctx.get("answerability", {}),
                    intent_contract=contract.model_dump(mode="json") if contract is not None else {},
                )
            else:
                ctx["contract_confirmation_state"] = confirmation_state
                if confirmation_request.action == "suspend":
                    contract = existing_draft
                    if contract is not None:
                        ctx["research_intent_contract"] = contract.model_dump(mode="json")
                    reply_kind, reply = plan_reply(
                        ctx,
                        user_input,
                        api_key=api_key,
                        provider_url=provider_url,
                        model=model,
                        on_delta=on_reply_delta,
                        run_dir=run_dir,
                    )
                    return OrchestratorResult(
                        reply=reply,
                        reply_kind=reply_kind,
                        evidence_used=ctx.get("usable_evidence", []),
                        answerability=ctx.get("answerability", {}),
                        intent_contract=contract.model_dump(mode="json") if contract is not None else {},
                    )
                if confirmation_request.action == "resume":
                    contract = existing_draft
                    if contract is not None and confirmation_state["status"] == "pending":
                        return OrchestratorResult(
                            reply=format_contract_for_user(contract),
                            reply_kind="intent_contract_confirmation",
                            evidence_used=ctx.get("usable_evidence", []),
                            answerability=ctx.get("answerability", {}),
                            intent_contract=contract.model_dump(mode="json"),
                        )
                elif confirmation_request.action == "supersede":
                    if not route_decision.contract_mutation_request.requested:
                        return OrchestratorResult(
                            reply="已停止旧草案的确认，旧记录仍会保留。要开始新方向时，请直接说明新的研究目标。",
                            reply_kind="intent_contract_superseded",
                            evidence_used=ctx.get("usable_evidence", []),
                            answerability=ctx.get("answerability", {}),
                        )

        if route_decision.contract_mutation_request.requested:
            outcome = interpret_and_apply_intent_mutation(
                run_dir=run_dir,
                user_input=user_input,
                persisted_contract=existing_draft,
                recent_mutation_receipts=_recent_mutation_receipts(run_dir),
                recent_dialogue=ctx.get("recent_dialogue", []) or [],
                active_sources=_source_registry_sources(run_dir),
                usable_evidence=ctx.get("usable_evidence", []) or [],
                unusable_evidence=ctx.get("unusable_parsed_sources", []) or [],
                jobs=ctx.get("jobs", []) or [],
                pending_confirmation=load_pending_contract_confirmation(run_dir),
                api_key=api_key,
                provider_url=provider_url,
                model=model,
            )
            receipt = outcome.receipt
            contract = receipt.contract
            if outcome.interpretation is not None:
                ctx["advisory_suggestions"] = [
                    item.model_dump(mode="json")
                    for item in outcome.interpretation.advisory_suggestions
                ]
                ctx["material_observation_proposals"] = [
                    item.model_dump(mode="json")
                    for item in outcome.interpretation.material_observations
                ]
            if contract is not None:
                ctx["research_intent_contract"] = contract.model_dump(mode="json")
            task_update = (
                _maybe_update_task_profile(
                    run_dir=run_dir,
                    turn_decision=turn_decision,
                    contract=contract,
                    on_task_updated=on_task_updated,
                )
                if receipt.status == "applied" and contract is not None
                else {}
            )
            _emit_progress(on_progress, "正在准备回复……")
            if receipt.status == "applied" and contract is not None and contract.ready_for_plan:
                request_contract_confirmation(run_dir, contract)
                reply_kind = "intent_contract_confirmation"
                reply = _combine_source_and_contract_reply(
                    created_sources,
                    created_jobs,
                    format_contract_for_user(contract),
                )
            else:
                reply_kind = "intent_contract_updated" if receipt.status == "applied" else "intent_contract_unchanged"
                reply = _mutation_receipt_reply(
                    receipt,
                    created_sources=created_sources,
                    created_jobs=created_jobs,
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
                task_update=task_update,
                mutation_receipt=receipt.model_dump(mode="json", exclude={"contract"}),
            )

        if confirmation_request.requested and confirmation_request.action == "request_pending":
            contract = existing_draft
            if contract is not None and contract.ready_for_plan:
                request_contract_confirmation(run_dir, contract)
                return OrchestratorResult(
                    reply=format_contract_for_user(contract),
                    reply_kind="intent_contract_confirmation",
                    created_sources=created_sources,
                    created_jobs=created_jobs,
                    evidence_used=ctx.get("usable_evidence", []),
                    answerability=ctx.get("answerability", {}),
                    next_actions=_suggest_next_actions(ctx, "intent_contract_confirmation"),
                    intent_contract=contract.model_dump(mode="json"),
                )
            return OrchestratorResult(
                reply=_confirmation_not_ready_reply(contract),
                reply_kind="intent_contract_unchanged",
                created_sources=created_sources,
                created_jobs=created_jobs,
                evidence_used=ctx.get("usable_evidence", []),
                answerability=ctx.get("answerability", {}),
                intent_contract=contract.model_dump(mode="json") if contract is not None else {},
            )

        if created_sources or created_jobs:
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
            )

        if attachments and route_decision.conversation_intents == ["source_request"]:
            return OrchestratorResult(
                reply="已收到附件；资料登记和解析状态会在右侧更新。",
                reply_kind="source_intake",
                evidence_used=ctx.get("usable_evidence", []),
                answerability=ctx.get("answerability", {}),
                intent_contract=existing_draft.model_dump(mode="json") if existing_draft is not None else {},
            )

        contract = existing_draft
        if contract is not None:
            ctx["research_intent_contract"] = contract.model_dump(mode="json")
        reply_kind, reply = plan_reply(
            ctx,
            user_input,
            api_key=api_key,
            provider_url=provider_url,
            model=model,
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


_MUTATION_FIELD_LABELS = {
    "research_goal": "研究目标",
    "research_object": "研究对象",
    "target_platform": "目标平台",
    "workload": "工作负载",
    "baseline": "基线",
    "dataset": "数据集",
    "evaluation_protocol": "评估协议",
    "primary_metrics": "主要指标",
    "secondary_metrics": "次要指标",
    "metric_priority": "指标优先级",
    "success_criteria": "成功标准",
    "compute_environment": "计算环境",
    "execution_mode": "执行边界",
    "user_improvement_hints": "用户方法偏好",
    "user_target_module_hints": "用户目标模块偏好",
    "preferred_method_hints": "用户方法偏好",
    "risk_preference": "风险偏好",
    "allowed_change_scope": "允许修改范围",
}


def _recent_mutation_receipts(run_dir: Path) -> list[dict[str, Any]]:
    return [
        event.get("payload", {})
        for event in load_events_since(run_dir)
        if event.get("type") == "contract.mutation.applied"
        and isinstance(event.get("payload"), dict)
    ][-3:]


def _mutation_receipt_reply(
    receipt: MutationReceipt,
    *,
    created_sources: list[dict[str, Any]],
    created_jobs: list[dict[str, Any]],
) -> str:
    parts: list[str] = []
    if created_sources or created_jobs:
        _, source_reply = _source_intake_reply(created_sources, created_jobs)
        parts.append(source_reply)
    if receipt.status == "applied":
        labels = _unique_strings([
            _MUTATION_FIELD_LABELS.get(field)
            for field in receipt.changed_fields
        ])
        parts.append(
            "已保存本轮研究设定"
            + ("：" + "、".join(labels) if labels else "")
            + "。"
        )
        contract = receipt.contract
        if contract is not None:
            question = next(
                (item.question for item in contract.open_questions if item.required_now),
                None,
            )
            if question:
                parts.append(question)
    elif receipt.reason == "no_operations":
        parts.append("本轮没有可落盘的新研究设定，草案保持不变。")
    else:
        parts.append("本轮研究设定未写入草案，现有草案保持不变。请重试或换一种表述。")
    return "\n".join(parts)


def _combine_source_and_contract_reply(
    created_sources: list[dict[str, Any]],
    created_jobs: list[dict[str, Any]],
    contract_reply: str,
) -> str:
    if not created_sources and not created_jobs:
        return contract_reply
    _, source_reply = _source_intake_reply(created_sources, created_jobs)
    return source_reply + "\n\n" + contract_reply


def _confirmation_not_ready_reply(contract: ResearchIntentContract | None) -> str:
    if contract is None:
        return "当前没有可确认的研究草案；聊天中的确认不会创建正式合同。请先说明研究目标和执行边界。"
    question = next(
        (item.question for item in contract.open_questions if item.required_now),
        None,
    )
    if question:
        return "当前草案尚未达到确认条件，未创建正式合同。\n" + question
    return (
        "当前草案尚未达到确认条件，未创建正式合同。"
        "请补充仍缺少的研究目标、研究对象、评价标准或执行边界。"
    )


def _maybe_update_task_profile(
    *,
    run_dir: Path,
    turn_decision,
    contract: ResearchIntentContract,
    on_task_updated: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    if (
        turn_decision.contract_action != "update_contract"
        or not task_profile_needs_automatic_title(run_dir)
    ):
        return {}
    generated = build_automatic_task_profile(
        run_id=run_dir.name,
        suggested_title=turn_decision.suggested_task_title,
        suggested_summary=turn_decision.suggested_task_summary,
        user_intent_summary=turn_decision.user_intent_summary,
        task_profile=turn_decision.task_profile_proposal,
        task_profile_evidence=turn_decision.task_profile_evidence,
        contract=contract.model_dump(mode="json"),
    )
    if generated is None:
        return {}
    updated = apply_automatic_task_profile(
        run_dir=run_dir,
        generated_profile=generated,
        updated_at=datetime.now(timezone.utc),
    )
    if updated is None:
        return {}
    payload = {
        "run_id": updated.run_id,
        "task_title": updated.task_title,
        "task_summary": updated.task_summary,
        "task_source": updated.source,
        "updated_at": updated.updated_at.isoformat() if updated.updated_at is not None else None,
    }
    if on_task_updated is not None:
        try:
            on_task_updated(payload)
        except Exception:
            pass
    return payload


def _emit_progress(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        pass


def _append_conversation_route_decided_event(
    run_dir: Path,
    decision: ConversationRouteDecision,
    *,
    mode: str,
) -> None:
    actions = decision.source_action_plan.actions
    turn = decision.turn_gate
    append_typed_event(run_dir, "planner.conversation_route.decided", {
        "routing_mode": mode,
        "action_count": len(actions),
        "action_types": _unique_strings([action.action_type for action in actions]),
        "source_kinds": _unique_strings([action.source_kind for action in actions if action.source_kind]),
        "requires_confirmation_count": sum(1 for action in actions if action.requires_confirmation),
        "source_confidence": decision.source_action_plan.confidence,
        "turn_type": turn.turn_type,
        "contract_action": turn.contract_action,
        "contract_update_allowed": turn.contract_update_allowed,
        "need_discovery_allowed": turn.need_discovery_allowed,
        "save_draft_allowed": turn.save_draft_allowed,
        "confirmation_action_proposal": turn.confirmation_action_proposal,
        "task_profile_proposal": decision.task_profile_proposal,
        "requires_need_discovery_enrichment": decision.requires_need_discovery_enrichment,
        "turn_confidence": turn.confidence,
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
