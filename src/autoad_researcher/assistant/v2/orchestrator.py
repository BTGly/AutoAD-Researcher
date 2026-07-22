"""Two-call V2 research dialogue orchestrator with a deterministic gate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.dialogue_gate import DialogueGate
from autoad_researcher.assistant.v2.dialogue_state import append_dialogue_transition
from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.job_service import append_pipeline_job, load_pipeline_jobs
from autoad_researcher.assistant.v2.research_dialogue_agent import (
    DialogueMode,
    DatasetSourceInstruction,
    GatedDialogueDecision,
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
from autoad_researcher.ui.sources import load_source_registry, register_local_dataset_source


@dataclass
class OrchestratorResult:
    reply: str = ""
    reply_kind: str = "answer"
    created_sources: list[dict[str, Any]] = field(default_factory=list)
    created_jobs: list[dict[str, Any]] = field(default_factory=list)
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
        on_reply_delta: Callable[[str], None] | None = None,
    ) -> OrchestratorResult:
        user_input = user_input.strip()
        if not user_input:
            return OrchestratorResult(reply="请输入问题。")

        created_sources: list[dict[str, Any]] = []
        created_jobs: list[dict[str, Any]] = []
        source_plan = plan_explicit_source_actions(
            user_input=user_input,
            attachments=attachments,
        )
        if source_plan is not None:
            created_sources, created_jobs = _execute_source_action_plan(
                run_dir,
                user_input,
                source_plan,
            )
        context = build_llm_context(run_dir, transcript_tail=transcript_tail)
        registered_sources = _registered_source_context(run_dir)
        context["registered_sources"] = registered_sources
        context["current_turn_material_actions"] = {
            "created_sources": created_sources,
            "created_jobs": created_jobs,
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
        )
        decision = DialogueGate.validate(
            candidate,
            run_dir=run_dir,
            registered_sources=registered_sources,
        )
        dataset_source_registration_failed = False
        if decision.dataset_source is not None:
            try:
                created_sources.append(
                    register_local_dataset_source(
                        run_dir,
                        decision.dataset_source.source_path,
                        user_label=decision.dataset_source.user_label,
                    )
                )
            except ValueError as exc:
                dataset_source_registration_failed = True
                append_event(
                    run_dir,
                    "assistant.dataset_source.registration_failed",
                    {"exception_type": type(exc).__name__},
                )
        if not candidate.is_valid:
            if not api_key:
                failure_reply = "当前没有可用的对话模型连接，材料任务仍可在后台处理。"
            elif not model.strip():
                failure_reply = "当前没有配置对话模型，材料任务仍可在后台处理。"
            else:
                failure_reply = "这轮意图判定失败了，请重试。"
            if on_reply_delta is not None:
                on_reply_delta(failure_reply)
            return OrchestratorResult(
                reply=failure_reply,
                created_sources=created_sources,
                created_jobs=created_jobs,
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
        ) and not dataset_source_registration_failed
        if reply_response.should_persist and task_draft_requested:
            try:
                draft, task_preparation_disposition = TaskBridge.prepare_or_reuse_experiment_task(
                    run_dir,
                    user_input=user_input,
                    transcript_tail=transcript_tail,
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
            dataset_source_registration_failed=dataset_source_registration_failed,
        )
        if on_reply_delta is not None:
            on_reply_delta(reply)
        return OrchestratorResult(
            reply=reply,
            created_sources=created_sources,
            created_jobs=created_jobs,
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
    dataset_source_registration_failed: bool = False,
) -> str:
    if dataset_source_registration_failed:
        return "本地数据集目录未能通过安全登记校验，因此尚未准备任务草案；请检查已配置的数据集目录后重试。"
    assessment = decision.policy_assessment
    if decision.policy == "deny" or assessment.decision == "reject":
        if reply_response.should_persist:
            return reply_response.visible_reply()
        return _policy_fallback(assessment.reason, assessment.safe_alternative)
    if experiment_task is not None:
        if reply_response.summary.blocking_question is not None:
            return (
                "研究任务草案已准备。"
                f"{reply_response.summary.blocking_question}"
                "这不阻止 plan_only 草案；实际运行前仍需完成该前置条件。"
            )
        if task_preparation_disposition == "reused":
            return "已有待确认的研究任务草案。请在界面中检查内容、选择执行模式并确认。"
        if task_preparation_disposition == "replaced":
            return "研究任务约束已更新，新的待确认草案已准备。请在界面中检查内容、选择执行模式并确认。"
        return "研究任务草案已准备。请在界面中检查内容、选择执行模式并确认。"
    if task_preparation_disposition == "prepare_failed":
        return "研究任务草案暂时无法准备；系统已保留诊断记录，请检查当前任务状态后重试。"
    if decision.dialogue_mode != "act" or decision.source_action is not None:
        return reply_response.visible_reply()
    if decision.execution_gate == "blocked_missing_contract":
        return (
            "我不能开始修改代码或运行实验：当前没有已确认的 input_task.yaml，"
            "自然语言中的“刚才确认”不能替代真实确认记录。请先完成研究任务准备与确认。"
        )
    return (
        "我已识别到执行请求，但当前 V2 对话入口只支持研究对齐和 plan_only 任务准备，"
        "不能在这里修改代码或运行实验。已确认的任务记录会保留，执行仍需独立的授权与 readiness gate。"
    )


def _policy_fallback(reason: str, safe_alternative: str) -> str:
    resolved_reason = reason.strip() or "该请求违反科研有效性或执行安全边界。"
    resolved_alternative = safe_alternative.strip()
    if not resolved_alternative:
        return resolved_reason
    return f"{resolved_reason}\n\n可行替代：{resolved_alternative}"


def _registered_source_context(run_dir: Path) -> list[dict[str, str]]:
    return [
        {
            "source_id": str(source.get("source_id") or ""),
            "kind": str(source.get("kind") or ""),
            "label": str(source.get("user_label") or source.get("stored_path") or ""),
            "status": str(source.get("status") or ""),
            "stored_path": str(source.get("stored_path") or ""),
        }
        for source in _source_registry_sources(run_dir)
        if source.get("source_id")
    ]


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
