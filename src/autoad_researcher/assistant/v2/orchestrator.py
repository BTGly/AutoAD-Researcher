"""Two-call V2 research dialogue orchestrator with a deterministic gate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

from autoad_researcher.assistant.model_routing import ModelRoute
from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.dialogue_gate import DialogueGate
from autoad_researcher.assistant.v2.dialogue_state import append_dialogue_transition
from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.job_service import (
    append_pipeline_job,
    create_or_get_pipeline_job,
    load_pipeline_jobs,
)
from autoad_researcher.assistant.v2.material_inspection import inspect_registered_material
from autoad_researcher.assistant.v2.research_dialogue_agent import (
    DialogueMode,
    GatedDialogueDecision,
    LocalPathSourceInstruction,
    ResearchDecisionAgent,
    ResearchReplyAgent,
    ResearchReplyResponse,
    SourceInstruction,
    TargetSpec,
)
from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    load_research_intent_summary,
    save_research_intent_summary,
)
from autoad_researcher.assistant.v2.source_actions import (
    SourceActionPlan,
    plan_explicit_source_actions,
)
from autoad_researcher.assistant.v2.source_service import register_source_intake
from autoad_researcher.assistant.v2.task_bridge import TaskBridge, TaskConfirmationConflict
from autoad_researcher.assistant.v2.target_adapter import get_target_adapter_registry
from autoad_researcher.ui.sources import (
    load_source_registry,
    register_local_path_source,
)
from autoad_researcher.ui.session_context import (
    attach_local_context,
    extract_local_path_candidates,
    load_session_context,
)


@dataclass
class OrchestratorResult:
    reply: str = ""
    reply_kind: str = "answer"
    created_sources: list[dict[str, Any]] = field(default_factory=list)
    created_jobs: list[dict[str, Any]] = field(default_factory=list)
    action_receipts: list[dict[str, Any]] = field(default_factory=list)
    material_action_status: str = "none"
    evidence_used: list[dict[str, Any]] = field(default_factory=list)
    answerability: dict[str, Any] = field(default_factory=dict)
    intent_summary: dict[str, Any] = field(default_factory=dict)
    source_action: dict[str, str] | None = None
    source_permission: dict[str, Any] | None = None
    experiment_task: dict[str, Any] | None = None
    dialogue_mode: DialogueMode = "ask"
    action_scope: str = "none"
    policy: str = "allow"
    evidence_status: str = "unavailable"
    conversation_transition: str = "new"
    feasibility: str = "not_assessed"
    numeric_claim_allowed: bool = True
    policy_assessment: dict[str, str] = field(default_factory=dict)


class ResearchOrchestratorV2:
    """Build context once, decide, gate, then generate the user reply."""

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
        model: str = "",
        temperature: float = 0.0,
        model_route: ModelRoute | None = None,
        on_reply_delta: Callable[[str], None] | None = None,
    ) -> OrchestratorResult:
        user_input = user_input.strip()
        if not user_input:
            return OrchestratorResult(reply="请输入问题。")

        created_sources: list[dict[str, Any]] = []
        created_jobs: list[dict[str, Any]] = []
        action_receipts: list[dict[str, Any]] = []
        local_contexts: list[dict[str, Any]] = []
        seen_turn_paths: set[str] = set()
        for explicit_path in extract_local_path_candidates(user_input, attachments):
            path_key = _context_path_key(explicit_path)
            if path_key in seen_turn_paths:
                continue
            seen_turn_paths.add(path_key)
            receipt, context_ref = _attach_local_context_action(
                run_dir,
                user_input=user_input,
                path=explicit_path,
                require_in_message=False,
            )
            if context_ref is not None:
                local_contexts.append(context_ref)
                source_receipt, source, jobs = _register_attached_local_source(
                    run_dir, path=explicit_path, context_ref=context_ref
                )
                action_receipts.append(source_receipt)
                if source is not None and source_receipt.get("status") != "already_registered":
                    created_sources.append(source)
                created_jobs.extend(jobs)
            else:
                action_receipts.append(receipt)
        source_plan = plan_explicit_source_actions(
            user_input=user_input,
            attachments=attachments,
        )
        if source_plan is not None:
            url_sources, url_jobs = _execute_source_action_plan(
                run_dir,
                user_input,
                source_plan,
            )
            created_sources.extend(url_sources)
            created_jobs.extend(url_jobs)
        context = build_llm_context(run_dir, transcript_tail=transcript_tail)
        registered_sources = _registered_source_context(run_dir)
        context["registered_sources"] = registered_sources
        context["session_contexts"] = load_session_context(run_dir)
        context["current_turn_material_actions"] = {
            "created_sources": created_sources,
            "created_jobs": created_jobs,
            "action_receipts": action_receipts,
        }
        context["pending_plan_only_task_available"] = TaskBridge.pending_plan_only_task_available(run_dir)
        previous = load_research_intent_summary(run_dir)
        candidate = ResearchDecisionAgent.decide(
            run_dir=run_dir,
            user_input=user_input,
            evidence_state=context,
            last_summary=previous,
            transcript_tail=transcript_tail,
            api_key=api_key,
            provider_url=provider_url,
            model=model,
            temperature=temperature,
            model_route=model_route,
        )
        decision = DialogueGate.validate(
            candidate,
            run_dir=run_dir,
            registered_sources=registered_sources,
        )
        local_sources: list[dict[str, Any]] = []
        local_actions = _local_source_instructions(decision)
        for local_action in local_actions:
            path_key = _context_path_key(local_action.source_path)
            if path_key in seen_turn_paths:
                continue
            seen_turn_paths.add(path_key)
            context_receipt, context_ref = _attach_local_context_action(
                run_dir,
                user_input=user_input,
                path=local_action.source_path,
                user_label=local_action.user_claimed_kind or "",
                user_hint=local_action.purpose or "",
            )
            if context_ref is not None:
                local_contexts.append(context_ref)
                source_receipt, source, jobs = _register_attached_local_source(
                    run_dir, path=local_action.source_path, context_ref=context_ref
                )
                action_receipts.append(source_receipt)
                if source is not None and source_receipt.get("status") != "already_registered":
                    created_sources.append(source)
                created_jobs.extend(jobs)
                local_sources.append(_inspection_source_for_context(context_ref))
            else:
                action_receipts.append(context_receipt)
        if local_actions:
            context["current_turn_material_actions"] = {
                "created_sources": created_sources,
                "created_jobs": created_jobs,
                "action_receipts": action_receipts,
                "material_action_status": _material_action_status(action_receipts),
            }
            registered_sources = _registered_source_context(run_dir)
            context["registered_sources"] = registered_sources
            context["session_contexts"] = load_session_context(run_dir)
        for context_ref in local_contexts:
            inspection_source = _inspection_source_for_context(context_ref)
            if inspection_source not in local_sources:
                local_sources.append(inspection_source)
        if not candidate.is_valid:
            if not api_key:
                failure_reply = "当前没有可用的对话模型连接，材料任务仍可在后台处理。"
            elif not model.strip():
                failure_reply = "当前没有配置对话模型，材料任务仍可在后台处理。"
            else:
                failure_reply = "这轮意图判定失败了，请重试。"
            if action_receipts:
                failure_reply = _material_receipt_reply(action_receipts) + "\n\n" + failure_reply
            if on_reply_delta is not None:
                on_reply_delta(failure_reply)
            return OrchestratorResult(
                reply=failure_reply,
                created_sources=created_sources,
                created_jobs=created_jobs,
                action_receipts=action_receipts,
                material_action_status=_material_action_status(action_receipts),
                evidence_used=context.get("usable_evidence", []),
                answerability=context.get("answerability", {}),
                intent_summary=(
                    (previous or ResearchIntentSummary()).model_dump(mode="json")
                ),
                dialogue_mode=decision.dialogue_mode,
                action_scope=decision.action_scope,
                policy=decision.policy,
                evidence_status=decision.evidence_status,
                conversation_transition=decision.conversation_transition,
                feasibility=decision.feasibility,
                numeric_claim_allowed=decision.numeric_claim_allowed,
                policy_assessment=decision.policy_assessment.model_dump(mode="json"),
            )

        observations: list[dict[str, Any]] = []
        for local_source in local_sources:
            observations.extend(inspect_registered_material(
                run_dir,
                source=local_source,
                user_input=user_input,
                api_key=api_key,
                provider_url=provider_url,
                model=model,
                model_route=model_route,
            ))
        if observations:
            context["material_inspections"] = observations

        if DialogueGate.plan_only_confirmation_allowed(decision):
            try:
                confirmed = TaskBridge.confirm_pending_plan_only_task(run_dir)
            except TaskConfirmationConflict as exc:
                append_event(
                    run_dir,
                    "assistant.experiment_task.chat_confirmation_failed",
                    {"code": exc.code},
                )
                reply = "当前没有可安全确认的 plan_only 任务草案；请先在界面中检查或重新准备草案。"
                if on_reply_delta is not None:
                    on_reply_delta(reply)
                return OrchestratorResult(
                    reply=reply,
                    created_sources=created_sources,
                    created_jobs=created_jobs,
                    action_receipts=action_receipts,
                    material_action_status=_material_action_status(action_receipts),
                    evidence_used=context.get("usable_evidence", []),
                    answerability=context.get("answerability", {}),
                    intent_summary=(previous or ResearchIntentSummary()).model_dump(mode="json"),
                    experiment_task=None,
                    dialogue_mode=decision.dialogue_mode,
                    action_scope=decision.action_scope,
                    policy=decision.policy,
                    evidence_status=decision.evidence_status,
                    conversation_transition=decision.conversation_transition,
                    feasibility=decision.feasibility,
                    numeric_claim_allowed=decision.numeric_claim_allowed,
                    policy_assessment=decision.policy_assessment.model_dump(mode="json"),
                )
            confirmed_summary = load_research_intent_summary(run_dir) or ResearchIntentSummary()
            append_dialogue_transition(
                run_dir,
                decision=decision,
                summary=confirmed_summary,
            )
            append_event(
                run_dir,
                "assistant.experiment_task.confirmed_from_chat",
                {"task_id": confirmed.task_id, "execution_mode": confirmed.execution_mode},
            )
            reply = "已确认现有的 plan_only 任务草案；未创建 Session、环境 Job 或实验执行。"
            if on_reply_delta is not None:
                on_reply_delta(reply)
            return OrchestratorResult(
                reply=reply,
                created_sources=created_sources,
                created_jobs=created_jobs,
                action_receipts=action_receipts,
                material_action_status=_material_action_status(action_receipts),
                evidence_used=context.get("usable_evidence", []),
                answerability=context.get("answerability", {}),
                intent_summary=confirmed_summary.model_dump(mode="json"),
                experiment_task=confirmed.model_dump(mode="json"),
                dialogue_mode=decision.dialogue_mode,
                action_scope=decision.action_scope,
                policy=decision.policy,
                evidence_status=decision.evidence_status,
                conversation_transition=decision.conversation_transition,
                feasibility=decision.feasibility,
                numeric_claim_allowed=decision.numeric_claim_allowed,
                policy_assessment=decision.policy_assessment.model_dump(mode="json"),
            )

        reply_response = ResearchReplyAgent.respond(
            run_dir=run_dir,
            user_input=user_input,
            evidence_state=context,
            frozen_decision=decision,
            last_summary=previous,
            transcript_tail=transcript_tail,
            api_key=api_key,
            provider_url=provider_url,
            model=model,
            temperature=temperature,
            model_route=model_route,
            on_reply_delta=None,
        )
        if reply_response.should_persist:
            save_research_intent_summary(run_dir, reply_response.summary)
        actions_allowed = (
            reply_response.should_persist
            and decision.policy == "allow"
            and decision.dialogue_mode in {"ask", "plan"}
        )
        target_job = _queue_repository_target_spec(
            run_dir,
            decision.target_spec if actions_allowed else None,
            created_sources=created_sources,
            created_jobs=created_jobs,
        )
        if target_job is not None:
            created_jobs.append(target_job)
        source_action = _validate_source_action(run_dir, decision.source_action)
        source_job = _dispatch_allowed_source_action(
            run_dir,
            source_action,
            decision.source_permission,
        ) if reply_response.should_persist else None
        if source_job is not None:
            created_jobs.append(source_job)
        experiment_task = None
        task_preparation_disposition = None
        task_draft_requested = (
            DialogueGate.task_action_allowed(decision, reply_response.summary)
            or DialogueGate.missing_contract_execution_can_prepare_task(
                decision,
                reply_response.summary,
            )
            or (
                bool(local_actions)
                and bool(reply_response.summary.goal.strip())
                and reply_response.summary.blocking_question is None
                and decision.conversation_transition != "cancel"
            )
        )
        material_blockers = _material_blockers(action_receipts)
        if reply_response.should_persist and task_draft_requested:
            try:
                draft, task_preparation_disposition = TaskBridge.prepare_or_reuse_experiment_task(
                    run_dir,
                    user_input=user_input,
                    transcript_tail=transcript_tail,
                    material_blockers=material_blockers,
                )
                if draft is not None:
                    experiment_task = draft.model_dump(mode="json")
                if task_preparation_disposition == "replaced":
                    append_event(
                        run_dir,
                        "assistant.experiment_task.replaced",
                        {"task_id": draft.task_id if draft is not None else ""},
                    )
            except (FileExistsError, ValueError) as exc:
                experiment_task = None
                task_preparation_disposition = "prepare_failed"
                append_event(
                    run_dir,
                    "assistant.experiment_task.prepare_failed",
                    {"exception_type": type(exc).__name__},
                )

        if reply_response.should_persist:
            append_dialogue_transition(
                run_dir,
                decision=decision,
                summary=reply_response.summary,
            )

        reply = _validated_dialogue_reply(
            decision,
            reply_response,
            experiment_task=experiment_task,
            task_preparation_disposition=task_preparation_disposition,
            action_receipts=action_receipts,
        )
        if on_reply_delta is not None:
            on_reply_delta(reply)
        return OrchestratorResult(
            reply=reply,
            created_sources=created_sources,
            created_jobs=created_jobs,
            action_receipts=action_receipts,
            material_action_status=_material_action_status(action_receipts),
            evidence_used=context.get("usable_evidence", []),
            answerability=context.get("answerability", {}),
            intent_summary=reply_response.summary.model_dump(mode="json"),
            source_action=(
                source_action.model_dump(mode="json")
                if source_action is not None
                else None
            ),
            source_permission=decision.source_permission,
            experiment_task=experiment_task,
            dialogue_mode=decision.dialogue_mode,
            action_scope=decision.action_scope,
            policy=decision.policy,
            evidence_status=decision.evidence_status,
            conversation_transition=decision.conversation_transition,
            feasibility=decision.feasibility,
            numeric_claim_allowed=decision.numeric_claim_allowed,
            policy_assessment=decision.policy_assessment.model_dump(mode="json"),
        )


def _validated_dialogue_reply(
    decision: GatedDialogueDecision,
    reply_response: ResearchReplyResponse,
    *,
    experiment_task: dict[str, Any] | None = None,
    task_preparation_disposition: str | None = None,
    action_receipts: list[dict[str, Any]] | None = None,
) -> str:
    material_status = _material_receipt_reply(action_receipts or [])
    assessment = decision.policy_assessment
    if decision.policy == "deny" or assessment.decision == "reject":
        if reply_response.should_persist:
            return reply_response.visible_reply()
        return _policy_fallback(assessment.reason, assessment.safe_alternative)
    if experiment_task is not None:
        if experiment_task.get("status") == "blocked_by_materials":
            return f"{material_status}\n\n研究任务草案已保存，但当前被资料状态阻塞；资料问题解决后才能确认或执行。"
        if reply_response.summary.blocking_question is not None:
            reply = (
                "研究任务草案已准备。"
                f"{reply_response.summary.blocking_question}"
                "这不阻止 plan_only 草案；实际运行前仍需完成该前置条件。"
            )
            return f"{material_status}\n\n{reply}" if material_status else reply
        if task_preparation_disposition == "reused":
            reply = "已有待确认的研究任务草案。请在界面中检查内容、选择执行模式并确认。"
            return f"{material_status}\n\n{reply}" if material_status else reply
        if task_preparation_disposition == "replaced":
            reply = "研究任务约束已更新，新的待确认草案已准备。请在界面中检查内容、选择执行模式并确认。"
            return f"{material_status}\n\n{reply}" if material_status else reply
        reply = "研究任务草案已准备。请在界面中检查内容、选择执行模式并确认。"
        return f"{material_status}\n\n{reply}" if material_status else reply
    if task_preparation_disposition == "prepare_failed":
        reply = "研究任务草案暂时无法准备；系统已保留诊断记录，请检查当前任务状态后重试。"
        return f"{material_status}\n\n{reply}" if material_status else reply
    if decision.dialogue_mode != "act" or decision.source_action is not None:
        reply = reply_response.visible_reply()
        return f"{material_status}\n\n{reply}" if material_status else reply
    if decision.execution_gate == "blocked_missing_contract":
        return (
            "我不能开始修改代码或运行实验：当前没有已确认的 input_task.yaml，"
            "自然语言中的“刚才确认”不能替代真实确认记录。请先完成研究任务准备与确认。"
        )
    reply = (
        "当前对话不会替你选择或切换执行模式；自动执行与逐步确认不能同时生效。"
        "当前不会修改代码或运行实验，请在实验工作台确认唯一执行模式，"
        "随后由独立授权与 readiness gate 继续。"
    )
    return f"{material_status}\n\n{reply}" if material_status else reply


def _material_action_status(receipts: list[dict[str, Any]]) -> str:
    if not receipts:
        return "none"
    succeeded = {"created", "already_registered", "job_queued", "context_attached", "context_already_attached"}
    successes = sum(1 for receipt in receipts if receipt.get("status") in succeeded)
    if successes == len(receipts):
        return "all_succeeded"
    if successes:
        return "partial_success"
    return "all_failed"


def _material_blockers(receipts: list[dict[str, Any]]) -> list[str]:
    return [
        f"{receipt.get('source_path') or '本地路径'}：{receipt.get('reason') or '登记失败'}"
        for receipt in receipts
        if receipt.get("status") in {"rejected", "failed"}
    ]


def _material_receipt_reply(receipts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    registered_paths = {
        str(receipt.get("source_path") or "")
        for receipt in receipts
        if receipt.get("status") in {"job_queued", "created", "already_registered"}
    }
    for receipt in receipts:
        path = str(receipt.get("source_path") or "本地路径")
        status = str(receipt.get("status") or "failed")
        if status in {"context_attached", "context_already_attached"}:
            if path in registered_paths:
                continue
            label = "已加入" if status == "context_attached" else "已在"
            lines.append(f"本地资料 {path}{label}当前会话的只读上下文，尚未纳入正式实验资料。")
        elif status == "job_queued":
            jobs = ", ".join(str(item) for item in receipt.get("job_ids") or [] if item)
            suffix = f"，处理任务 {jobs} 已排队" if jobs else "，处理任务已排队"
            source_id = str(receipt.get("source_id") or "")
            source_status = str(receipt.get("source_status") or "")
            identity = f"（资料 {source_id}，状态 {source_status}）" if source_id and source_status else ""
            lines.append(f"本地资料 {path} 已登记{identity}{suffix}。")
        elif status in {"created", "already_registered"}:
            source_id = str(receipt.get("source_id") or "")
            identity = f"（资料 {source_id}）" if source_id else ""
            if receipt.get("kind") == "local_path":
                lines.append(f"本地资料 {path} 已登记{identity}，资料角色待确认，未创建不匹配的处理任务。")
            else:
                lines.append(f"本地资料 {path} 已登记{identity}。")
        else:
            lines.append(f"本地资料 {path} 未登记：{receipt.get('reason') or '登记失败'}。")
    return "\n".join(lines)


def _policy_fallback(reason: str, safe_alternative: str) -> str:
    resolved_reason = reason.strip() or "该请求违反科研有效性或执行安全边界。"
    resolved_alternative = safe_alternative.strip()
    if not resolved_alternative:
        return resolved_reason
    return f"{resolved_reason}\n\n可行替代：{resolved_alternative}"


def _registered_source_context(run_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "source_id": str(source.get("source_id") or ""),
            "kind": str(source.get("kind") or ""),
            "label": str(source.get("user_label") or source.get("stored_path") or ""),
            "status": str(source.get("status") or ""),
            "stored_path": str(source.get("stored_path") or ""),
            "original_reference": str(source.get("original_reference") or ""),
            "inspection": (source.get("metadata") or {}).get("local_path_inspection", {}),
        }
        for source in _source_registry_sources(run_dir)
        if source.get("source_id")
    ]


def _local_source_instructions(
    decision: GatedDialogueDecision,
) -> list[LocalPathSourceInstruction]:
    if decision.local_path_sources:
        return list(decision.local_path_sources)
    return [decision.local_path_source] if decision.local_path_source is not None else []


def _local_source_instruction(
    decision: GatedDialogueDecision,
) -> LocalPathSourceInstruction | None:
    instructions = _local_source_instructions(decision)
    return instructions[0] if instructions else None


def _attach_local_context_action(
    run_dir: Path,
    *,
    user_input: str,
    path: str,
    user_label: str = "",
    user_hint: str = "",
    require_in_message: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if require_in_message and path not in user_input:
        receipt = {
            "kind": "session_context",
            "source_path": path,
            "status": "rejected",
            "reason": "source_path_not_present_in_current_user_message",
        }
        append_event(run_dir, "assistant.local_context.attach_rejected", receipt)
        return receipt, None
    receipt, context_ref = attach_local_context(
        run_dir,
        path,
        user_label=user_label,
        user_hint=user_hint,
    )
    event_type = (
        "assistant.local_context.attached"
        if receipt.get("status") in {"context_attached", "context_already_attached"}
        else "assistant.local_context.attach_failed"
    )
    append_event(run_dir, event_type, receipt)
    return receipt, context_ref


def _context_path_key(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        return str(path)


def _local_workspace_roots(run_dir: Path, path: str) -> list[Path]:
    """Preserve the session-context boundary for sibling Run workspaces."""

    roots = [run_dir / "workspace"]
    configured_root = Path(os.environ.get("AUTOAD_RUNS_ROOT", "runs")).expanduser().resolve()
    candidate = Path(path).expanduser().resolve(strict=False)
    for ancestor in (candidate, *candidate.parents):
        if ancestor.name != "workspace":
            continue
        try:
            ancestor.relative_to(configured_root)
        except ValueError:
            continue
        if ancestor not in roots:
            roots.append(ancestor)
    return roots


def _inspection_source_for_context(context_ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": context_ref.get("context_id", ""),
        "kind": "context",
        "context_only": True,
        "original_reference": context_ref.get("path", ""),
        "inspection": context_ref.get("inspection", {}),
    }


def _register_attached_local_source(
    run_dir: Path, *, path: str, context_ref: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """Promote an already-authorized local context into durable source intake."""

    try:
        source = register_local_path_source(
            run_dir,
            path,
            user_label=str(context_ref.get("user_label") or ""),
            additional_allowed_roots=_local_workspace_roots(run_dir, path),
        )
        jobs = _queue_local_path_jobs(run_dir, source)
        receipt = {
            "kind": source.get("kind", "local_path"),
            "source_path": path,
            "source_id": source.get("source_id", ""),
            "status": "job_queued" if jobs else source.get("receipt_status", "created"),
            "source_status": source.get("status", ""),
            "job_ids": [job.get("job_id", "") for job in jobs],
            "inspection": source.get("inspection", {}),
        }
        append_event(run_dir, "assistant.local_source.registered", receipt)
        return receipt, source, jobs
    except (OSError, ValueError) as exc:
        receipt = {
            "kind": "local_path", "source_path": path, "status": "failed",
            "reason": str(exc),
        }
        append_event(run_dir, "assistant.local_source.registration_failed", receipt)
        return receipt, None, []


def _queue_local_path_jobs(run_dir: Path, source: dict[str, Any]) -> list[dict[str, Any]]:
    source_id = str(source.get("source_id") or "")
    if not source_id:
        return []
    if source.get("kind") == "local_repo":
        acquire, acquire_created = create_or_get_pipeline_job(
            run_dir,
            source_id=source_id,
            job_type="local_repo_acquire",
            idempotency_key=f"local-source:{source_id}:local_repo_acquire",
            evidence_role="repo_acquired",
            payload={"source_role": "local_repo"},
        )
        summarize, summarize_created = create_or_get_pipeline_job(
            run_dir,
            source_id=source_id,
            job_type="repo_summarize",
            idempotency_key=f"local-source:{source_id}:repo_summarize",
            evidence_role="repo_acquired",
            payload={"depends_on": acquire.get("job_id"), "source_role": "local_repo"},
        )
        return [job for job, created in ((acquire, acquire_created), (summarize, summarize_created)) if created]

    kind = str(source.get("kind") or "")
    if kind == "dataset":
        job, created = create_or_get_pipeline_job(
            run_dir,
            source_id=source_id,
            job_type="dataset_manifest",
            idempotency_key=f"local-source:{source_id}:dataset_manifest",
            evidence_role="dataset_manifest",
            payload={
                "original_reference": source.get("original_reference", ""),
                "manifest_path": source.get("manifest_path", ""),
                "source_role": "dataset",
            },
        )
        return [job] if created else []
    stored_path = str(source.get("stored_path") or "")
    if not stored_path:
        return []
    job_type_by_kind = {
        "paper_pdf": ("paper_parse_mineru", "parsed_paper_evidence"),
        "document": ("document_markitdown", "parsed_document_evidence"),
        "archive_bundle": ("archive_unpack_classify", "archive_manifest"),
    }
    spec = job_type_by_kind.get(kind)
    if spec is None:
        return []
    job_type, evidence_role = spec
    job, created = create_or_get_pipeline_job(
        run_dir,
        source_id=source_id,
        job_type=job_type,
        idempotency_key=f"local-source:{source_id}:{job_type}",
        evidence_role=evidence_role,
        payload={"stored_path": stored_path, "source_role": "local_file"},
    )
    return [job] if created else []


def _validate_source_action(
    run_dir: Path,
    action: SourceInstruction | None,
) -> SourceInstruction | None:
    if action is None:
        return None
    source_ids = {
        str(source.get("source_id") or "")
        for source in _source_registry_sources(run_dir)
    }
    return action if action.source_id in source_ids else None


def _dispatch_allowed_source_action(
    run_dir: Path,
    action: SourceInstruction | None,
    permission: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if (
        action is None
        or action.action != "request_source_reparse"
        or permission is None
        or permission.get("permission_decision") != "allow"
    ):
        return None
    existing = [
        job for job in load_pipeline_jobs(run_dir)
        if job.get("source_id") == action.source_id
        and job.get("job_type") == "paper_parse_mineru"
        and job.get("status") in {"queued", "running"}
        and isinstance(job.get("payload"), dict)
        and job["payload"].get("requested_action") == action.action
    ]
    if existing:
        return None
    job = append_pipeline_job(
        run_dir,
        source_id=action.source_id,
        job_type="paper_parse_mineru",
        evidence_role="parsed_paper_evidence",
        payload={
            "requested_action": action.action,
            "source_action": action.model_dump(mode="json"),
        },
    )
    append_event(
        run_dir,
        "source.reparse_queued",
        {"source_id": action.source_id, "job_id": job["job_id"]},
    )
    return job


def _execute_source_action_plan(
    run_dir: Path,
    user_input: str,
    source_plan: SourceActionPlan,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    created_sources: list[dict[str, Any]] = []
    created_jobs: list[dict[str, Any]] = []
    registered_urls: dict[str, dict[str, Any]] = {}

    for action in source_plan.actions:
        if action.requires_confirmation or action.action_type == "answer_only":
            continue
        if action.action_type not in {"register_webpage", "register_github_repo"}:
            continue
        if not action.source_url:
            continue
        source_kind = (
            "github_repo"
            if action.action_type == "register_github_repo"
            else "webpage"
        )
        source = registered_urls.get(action.source_url)
        if source is None:
            source = register_source_intake(
                run_dir,
                user_input=user_input,
                source_kind=source_kind,
                source_url=action.source_url,
            )
            registered_urls[action.source_url] = source
            created_sources.append(source)
        source_id = str(source.get("source_id", ""))
        if source_kind == "github_repo":
            clone = append_pipeline_job(
                run_dir,
                source_id=source_id,
                job_type="git_clone",
                evidence_role="candidate_source_only",
                payload={"source_action": action.model_dump(mode="json")},
            )
            created_jobs.append(clone)
            created_jobs.append(
                append_pipeline_job(
                    run_dir,
                    source_id=source_id,
                    job_type="repo_summarize",
                    evidence_role="repo_acquired",
                    payload={
                        "depends_on": clone.get("job_id"),
                        "source_action": action.model_dump(mode="json"),
                    },
                )
            )
        else:
            fetch = append_pipeline_job(
                run_dir,
                source_id=source_id,
                job_type="web_fetch",
                evidence_role="source_acquired_unparsed",
                payload={"source_action": action.model_dump(mode="json")},
            )
            created_jobs.append(fetch)
            created_jobs.append(
                append_pipeline_job(
                    run_dir,
                    source_id=source_id,
                    job_type="web_markitdown",
                    evidence_role="parsed_web_evidence",
                    payload={
                        "depends_on": fetch.get("job_id"),
                        "source_action": action.model_dump(mode="json"),
                    },
                )
            )
    return created_sources, created_jobs


def _source_registry_sources(run_dir: Path) -> list[dict[str, Any]]:
    sources = load_source_registry(run_dir).get("sources", [])
    return [item for item in sources if isinstance(item, dict)]


def _queue_repository_target_spec(
    run_dir: Path,
    target_spec: TargetSpec | None,
    *,
    created_sources: list[dict[str, Any]],
    created_jobs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if target_spec is None:
        return None
    target = get_target_adapter_registry().resolve(
        target_spec.adapter_id,
        target_spec.selectors,
    )
    if target is None:
        return None
    repository_sources = [
        source
        for source in created_sources
        if source.get("kind") in {"github_repo", "local_repo"}
    ]
    if len(repository_sources) > 1:
        return None
    if not repository_sources:
        repository_sources = [
            source
            for source in _source_registry_sources(run_dir)
            if source.get("kind") in {"github_repo", "local_repo"}
        ]
    if len(repository_sources) != 1:
        return None
    source = repository_sources[0]
    source_id = str(source.get("source_id") or "")
    if not source_id:
        return None
    payload = {
        "target_adapter_id": target.adapter_id,
        target.payload_key: target.selectors,
    }
    for job in load_pipeline_jobs(run_dir):
        if (
            job.get("source_id") == source_id
            and job.get("job_type") == target.job_type
            and (job.get("payload") or {}).get("target_adapter_id") == target.adapter_id
            and (job.get("payload") or {}).get(target.payload_key) == target.selectors
        ):
            return None
    clone = next(
        (
            job
            for job in created_jobs
            if job.get("source_id") == source_id and job.get("job_type") == "git_clone"
        ),
        None,
    )
    if clone is not None:
        payload["depends_on"] = clone.get("job_id")
    return append_pipeline_job(
        run_dir,
        source_id=source_id,
        job_type=target.job_type,
        evidence_role=target.evidence_role,
        payload=payload,
    )
