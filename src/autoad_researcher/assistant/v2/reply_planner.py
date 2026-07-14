"""Reply planner for V2 — LLM-first, answerability-driven fallback."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autoad_researcher.assistant.llm_runtime import runtime_trace_fields
from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.v2.llm_trace_service import append_llm_trace


class V2ReplyPlan(BaseModel):
    """Strict internal envelope; only visible fields may reach the user."""

    model_config = ConfigDict(extra="forbid")

    reply_to_user: str
    contract_updates: dict[str, Any]
    missing_required_fields: list[str]
    next_question: str
    ready_for_confirmation: bool
    new_user_confirmed_fields: list[str] = Field(default_factory=list)
    optional_hints_detected: dict[str, Any] = Field(default_factory=dict)
    ready_for_experiment_agents: bool = False
    ready_for_plan: bool = False
    primary_metrics: list[str] = Field(default_factory=list)
    secondary_metrics: list[str] = Field(default_factory=list)
    metric_priority: str | None = None


def plan_reply(
    llm_context: dict[str, Any],
    user_input: str,
    *,
    api_key: str = "",
    provider_url: str = "",
    model: str = "deepseek-v4-flash",
    on_delta: Callable[[str], None] | None = None,
    run_dir: Path | None = None,
) -> tuple[str, str]:
    """Return (reply_kind, reply_text).

    All user input goes through LLM when api_key is available.
    Fallback is evidence-state structured — no keyword matching, no fixed templates.
    """

    answerability = llm_context.get("answerability", {})
    blocking = answerability.get("blocking_next_step", "")
    usable = llm_context.get("usable_evidence", [])
    unparsed = llm_context.get("unparsed_sources", [])
    readable = llm_context.get("readable_summaries", [])
    unusable = llm_context.get("unusable_parsed_sources", [])
    pending_jobs = llm_context.get("pending_jobs", [])
    failed_jobs = llm_context.get("failed_jobs", [])
    turn_gate = llm_context.get("turn_gate_decision", {}) or {}
    if _is_explicit_repo_failure_question(user_input) and failed_jobs:
        return _job_failure_fallback(blocking, pending_jobs, failed_jobs)
    if _is_explicit_parse_failure_question(user_input) and (failed_jobs or unusable):
        return _parse_failure_fallback(blocking, pending_jobs, failed_jobs, unusable)

    if turn_gate.get("contract_action") in {"answer_without_contract_update", "ask_clarifying_question"}:
        if api_key:
            return _llm_reply(
                llm_context,
                user_input,
                api_key,
                provider_url,
                model=model,
                on_delta=on_delta,
                run_dir=run_dir,
            )
        return "answer", _non_contract_fallback(turn_gate)

    if api_key:
        return _llm_reply(
            llm_context,
            user_input,
            api_key,
            provider_url,
            model=model,
            on_delta=on_delta,
            run_dir=run_dir,
        )

    return _unified_fallback(blocking, len(unparsed), len(usable), len(readable), pending_jobs, failed_jobs, unusable)


def _llm_reply(
    llm_context: dict[str, Any],
    user_input: str,
    api_key: str,
    provider_url: str,
    *,
    model: str = "deepseek-v4-flash",
    on_delta: Callable[[str], None] | None = None,
    run_dir: Path | None = None,
) -> tuple[str, str]:
    readable = llm_context.get("readable_summaries", [])
    confirmed = llm_context.get("confirmed_from_user", {})
    contract = llm_context.get("research_intent_contract", {})
    turn_gate = llm_context.get("turn_gate_decision", {})
    pending_jobs = llm_context.get("pending_jobs", [])
    failed_jobs = llm_context.get("failed_jobs", [])
    unusable = llm_context.get("unusable_parsed_sources", [])
    paper_summaries = llm_context.get("paper_reading_summaries", [])
    artifact_manifests = llm_context.get("artifact_manifests", [])
    recent_dialogue = llm_context.get("recent_dialogue", [])
    blocking = llm_context.get("answerability", {}).get("blocking_next_step", "")
    evidence_text = "\n---\n".join(readable[:3]) if readable else "无可用 evidence"
    confirmed_text = "\n".join(f"{k}: {v}" for k, v in confirmed.items()) if confirmed else "无"
    contract_text = _json_text(contract) if contract else "{}"
    turn_gate_text = _json_text(_reply_planner_turn_gate_context(turn_gate)) if turn_gate else "{}"

    selector = PromptSelector()
    profile = selector.profile_for_v2_component("reply_planner")
    system = selector.build_system_prompt_for_v2_component("reply_planner")

    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": f"当前状态: {blocking or 'idle'}"},
        {"role": "system", "content": f"最近对话（按时间顺序）:\n{_json_text(recent_dialogue)}"},
        {"role": "system", "content": f"已确认事实:\n{confirmed_text}"},
        {"role": "system", "content": f"TurnGateDecision:\n{turn_gate_text}"},
        {"role": "system", "content": f"ResearchIntentContract draft:\n{contract_text}"},
        {"role": "system", "content": f"可用 evidence:\n{evidence_text}"},
        {"role": "system", "content": f"Paper reading summaries:\n{_json_text(paper_summaries)}"},
        {"role": "system", "content": f"Artifact manifests:\n{_json_text(artifact_manifests)}"},
        {"role": "system", "content": f"不可用解析结果:\n{_json_text(unusable)}"},
        {"role": "system", "content": f"后台 PipelineJobs:\n{_json_text({'pending_jobs': pending_jobs, 'failed_jobs': failed_jobs})}"},
        {"role": "user", "content": user_input},
    ]

    from autoad_researcher.ui.chat_client import call_research_chat
    started = time.perf_counter()
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model,
        timeout_s=30,
        # The response is an internal control envelope. Buffer it until the
        # complete payload passes schema and authorization validation.
        on_delta=None,
        priority="interactive",
        response_format_json=True,
    )
    latency_ms = (time.perf_counter() - started) * 1000

    if result.get("reply") and not result.get("error"):
        reply_text = str(result["reply"])
        payload, validation_errors = _parse_llm_contract_reply(reply_text)
        if payload is not None:
            if _requests_unbacked_confirmation(payload, contract):
                append_llm_trace(
                    run_dir,
                    call_site="reply_planner",
                    prompt_id=profile.prompt_id,
                    prompt_version=profile.prompt_version,
                    prompt_text=system,
                    model=model,
                    provider_url=provider_url,
                    messages=messages,
                    raw_output=reply_text,
                    parse_status="ok",
                    schema_validation="error",
                    schema_validation_errors=validation_errors,
                    fallback_reason="unbacked_confirmation_request",
                    latency_ms=latency_ms,
                    **runtime_trace_fields(result),
                )
                return _reply_failure_fallback(
                    turn_gate,
                    blocking,
                    pending_jobs,
                    failed_jobs,
                    unusable,
                )
            append_llm_trace(
                run_dir,
                call_site="reply_planner",
                prompt_id=profile.prompt_id,
                prompt_version=profile.prompt_version,
                prompt_text=system,
                model=model,
                provider_url=provider_url,
                messages=messages,
                raw_output=reply_text,
                parse_status="ok",
                schema_validation="ok",
                latency_ms=latency_ms,
                **runtime_trace_fields(result),
            )
            visible_reply = _visible_reply_from_llm_payload(payload)
            if on_delta is not None:
                on_delta(visible_reply)
            return "answer", visible_reply
        append_llm_trace(
            run_dir,
            call_site="reply_planner",
            prompt_id=profile.prompt_id,
            prompt_version=profile.prompt_version,
            prompt_text=system,
            model=model,
            provider_url=provider_url,
            messages=messages,
            raw_output=reply_text,
            parse_status="error",
            schema_validation="error" if validation_errors else "not_run",
            schema_validation_errors=validation_errors,
            fallback_reason="reply_plan_parse_or_schema_failed",
            latency_ms=latency_ms,
            **runtime_trace_fields(result),
        )
        return _reply_failure_fallback(
            turn_gate,
            blocking,
            pending_jobs,
            failed_jobs,
            unusable,
        )

    append_llm_trace(
        run_dir,
        call_site="reply_planner",
        prompt_id=profile.prompt_id,
        prompt_version=profile.prompt_version,
        prompt_text=system,
        model=model,
        provider_url=provider_url,
        messages=messages,
        raw_output=str(result.get("reply") or ""),
        parse_status="skipped",
        schema_validation="skipped",
        fallback_reason="llm_error_or_empty_reply",
        latency_ms=latency_ms,
        **runtime_trace_fields(result),
    )
    return _reply_failure_fallback(
        turn_gate,
        blocking,
        pending_jobs,
        failed_jobs,
        unusable,
    )


def _reply_failure_fallback(
    turn_gate: dict[str, Any],
    blocking: str,
    pending_jobs: list[dict[str, Any]],
    failed_jobs: list[dict[str, Any]],
    unusable_sources: list[dict[str, Any]],
) -> tuple[str, str]:
    if turn_gate.get("contract_action") in {"answer_without_contract_update", "ask_clarifying_question"}:
        return "answer", _non_contract_fallback(turn_gate)
    return _unified_fallback(blocking, 0, 0, 0, pending_jobs, failed_jobs, unusable_sources)


def _requests_unbacked_confirmation(payload: V2ReplyPlan, contract: Any) -> bool:
    return payload.ready_for_confirmation is True and not _has_ready_contract(contract)


def _has_ready_contract(contract: Any) -> bool:
    return isinstance(contract, dict) and contract.get("ready_for_plan") is True


def _unified_fallback(
    blocking: str,
    unparsed_count: int,
    usable_count: int,
    readable_count: int,
    pending_jobs: list[dict[str, Any]] | None = None,
    failed_jobs: list[dict[str, Any]] | None = None,
    unusable_sources: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """User-facing fallback derived from typed state, without internal control terms."""
    parts: list[str] = []
    pending_jobs = pending_jobs or []
    failed_jobs = failed_jobs or []
    unusable_sources = unusable_sources or []

    if unparsed_count:
        parts.append(f"还有 {unparsed_count} 份资料尚未解析。")
    if pending_jobs:
        parts.append(f"还有 {len(pending_jobs)} 项资料正在处理。")
        parts.append("这些任务完成前，我不会声称已经读完相应资料。")
    if failed_jobs:
        parts.append(f"有 {len(failed_jobs)} 项资料处理失败；可以继续查看具体资料的失败原因。")
    if unusable_sources:
        labels = ", ".join(
            str(item.get("user_label") or item.get("source_id"))
            for item in unusable_sources[:3]
        )
        parts.append(f"以下资料暂时无法读取：{labels}。")
        known_reasons = _known_unusable_reasons(unusable_sources)
        if known_reasons:
            parts.append("已知原因: " + "；".join(known_reasons[:3]))
        else:
            parts.append("当前只能确认它们没有产出可读正文。")
        parts.append("因此我不能据此提取论文方法或声称看过内容。")
    if usable_count:
        parts.append(f"已有 {usable_count} 条可用证据。")
    if readable_count:
        parts.append(f"已有 {readable_count} 份可读摘要。")

    parts.append("当前只需要确认研究目标和评估边界，不需要你先设计具体方法或指定要改哪个模块。")
    parts.append("已有改进想法可以直接告诉我；没有也不影响后续规划。")
    parts.append("请先确认主要目标：指标效果、推理速度、显存占用、训练成本、复现跑通，还是稳定性/泛化？")
    parts.append("在你明确确认前，我不会把对话当成修改代码或运行实验的授权。")

    return "answer", "\n".join(parts)


def _parse_failure_fallback(
    blocking: str,
    pending_jobs: list[dict[str, Any]] | None = None,
    failed_jobs: list[dict[str, Any]] | None = None,
    unusable_sources: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    parts = ["目前有资料没有成功转换成可读内容。"]
    pending_jobs = pending_jobs or []
    failed_jobs = failed_jobs or []
    unusable_sources = unusable_sources or []

    if failed_jobs:
        parts.append(f"共有 {len(failed_jobs)} 项相关资料处理失败。")
    if pending_jobs:
        parts.append(f"另有 {len(pending_jobs)} 项资料仍在处理中。")
    if unusable_sources:
        labels = ", ".join(
            str(item.get("user_label") or item.get("source_id"))
            for item in unusable_sources[:3]
        )
        parts.append(f"暂时无法读取的资料：{labels}")
        known_reasons = _known_unusable_reasons(unusable_sources)
        if known_reasons:
            parts.append("已知原因: " + "；".join(known_reasons[:3]))
        else:
            parts.append("当前只能确认这些资料没有产出可读正文。")
    parts.append("以上只基于当前保存的处理结果；我不会补充没有证据的原因。")
    if unusable_sources:
        parts.append("因此这些资料目前不能作为论文方法细节的依据。")
    return "answer", "\n".join(parts)


def _job_failure_fallback(
    blocking: str,
    pending_jobs: list[dict[str, Any]] | None = None,
    failed_jobs: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    pending_jobs = pending_jobs or []
    failed_jobs = failed_jobs or []
    repo_failures = [job for job in failed_jobs if str(job.get("job_type") or "") in {"git_clone", "repo_summarize", "repo_analyze"}]
    root_clone_failures = [job for job in repo_failures if str(job.get("job_type") or "") == "git_clone"]
    dependent_failures = [job for job in repo_failures if str(job.get("error") or "").startswith("dependency failed")]
    network_failures = [job for job in root_clone_failures if _looks_like_network_clone_failure(str(job.get("error") or ""))]

    if network_failures:
        parts = ["是的，从当前保存的处理结果看，主要原因是访问 GitHub 时的网络/TLS 传输失败。"]
    elif root_clone_failures:
        parts = ["当前根因在 git clone 阶段，仓库还没有成功拉到本地。"]
    else:
        parts = ["当前没有足够信息判断仓库获取失败的具体原因。"]

    if root_clone_failures:
        parts.append(f"仓库获取共失败 {len(root_clone_failures)} 次。")
    elif failed_jobs:
        parts.append(f"有 {len(failed_jobs)} 项相关资料处理失败。")
    if dependent_failures:
        parts.append("后续仓库分析也因获取失败而没有继续。")
    if pending_jobs:
        parts.append(f"另有 {len(pending_jobs)} 项资料仍在处理中。")
    if network_failures:
        parts.append("这更像是当前环境到 GitHub 的连接不稳定或被中断，不像是仓库不存在。")
        parts.append(
            "下一步更稳的做法是由用户提供一个当前环境可访问的仓库来源："
            "Gitee/GitCode/AtomGit 等镜像 URL，或本地 clone 后打包的 zip/tar。"
            "系统会把这些材料登记、解析、证据化，再继续仓库分析。"
        )
    elif repo_failures:
        parts.append("因此当前仓库还没有成功 clone/analysis，右侧 Evidence 不应把 repo 摘要当作可用证据。")
    return "answer", "\n".join(parts)


def _looks_like_network_clone_failure(error: str) -> bool:
    lowered = error.lower()
    return any(token in lowered for token in (
        "timed_out",
        "timeout",
        "gnutls",
        "tls connection",
        "non-properly terminated",
        "unable to access",
        "failed to connect",
        "connection reset",
        "cloning into",
        "recv error",
        "early eof",
        "curl",
    ))


def _is_explicit_parse_failure_question(user_input: str) -> bool:
    text = re.sub(r"\s+", "", str(user_input).strip().lower())
    if not text:
        return False
    has_failure_signal = any(token in text for token in ("失败", "报错", "错误", "原因", "为什么", "为啥", "怎么回事"))
    has_parse_subject = any(token in text for token in (
        "解析", "parse", "parser", "pdf", "论文", "文档", "资料", "source", "artifact", "mineru", "markitdown",
    ))
    return has_failure_signal and has_parse_subject


def _is_explicit_repo_failure_question(user_input: str) -> bool:
    text = re.sub(r"\s+", "", str(user_input).strip().lower())
    if not text:
        return False
    has_failure_signal = any(token in text for token in ("失败", "报错", "错误", "原因", "为什么", "为啥", "怎么回事"))
    has_repo_signal = any(token in text for token in ("clone", "git", "github", "repo", "仓库"))
    return has_failure_signal and has_repo_signal


def _non_contract_fallback(turn_gate: dict[str, Any] | None = None) -> str:
    turn_type = str((turn_gate or {}).get("turn_type") or "")
    if turn_type == "frustration":
        return (
            "刚才的回复与实际保存状态没有对齐，抱歉。我会以已保存状态为准；"
            "需要确认时会显示确认窗口，不会只靠聊天文字代替。"
        )
    if turn_type == "identity_question":
        return "我是 AutoAD Researcher，负责协助整理研究目标、资料证据和实验规划边界。"
    if turn_type in {"ordinary_chat", "joke"}:
        return "可以聊聊；当前研究任务会保持不变，你想继续时我们再接着推进。"
    return "我还不能可靠判断这句话是否要修改当前研究任务。你可以补充目标，或说明是继续、暂停还是取消当前任务。"


def _reply_planner_turn_gate_context(turn_gate: dict[str, Any]) -> dict[str, Any]:
    allowed_fields = ("turn_type", "contract_action", "user_intent_summary")
    return {
        field: turn_gate[field]
        for field in allowed_fields
        if field in turn_gate
    }


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return "{}"


def _parse_llm_contract_reply(text: str) -> tuple[V2ReplyPlan | None, list[dict[str, str]]]:
    stripped = text.strip()
    if not stripped:
        return None, []
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None, []
    try:
        return V2ReplyPlan.model_validate(payload), []
    except ValidationError as exc:
        errors = [
            {
                "loc": ".".join(str(part) for part in error.get("loc", ())) or "root",
                "type": str(error.get("type") or "validation_error"),
            }
            for error in exc.errors()
        ]
        return None, errors


def _visible_reply_from_llm_payload(payload: V2ReplyPlan) -> str:
    parts: list[str] = []
    reply = _clean_visible_text(payload.reply_to_user)
    question = _clean_visible_text(payload.next_question)
    if reply:
        parts.append(reply)
    if question and question != reply:
        parts.append(question)
    if not parts:
        parts.append("我已更新研究意图草稿。请继续补充目标、指标或成功标准。")
    return "\n\n".join(parts)


def _clean_visible_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _known_unusable_reasons(unusable_sources: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for item in unusable_sources:
        for warning in item.get("warnings") or []:
            if isinstance(warning, str) and warning.strip():
                reasons.append(warning.strip())
        for error in item.get("fatal_errors") or []:
            if isinstance(error, str) and error.strip():
                reasons.append(error.strip())
        for parser_error in item.get("parser_errors") or []:
            if not isinstance(parser_error, dict):
                continue
            parser = str(parser_error.get("parser_name") or "parser")
            error = str(parser_error.get("error") or "").strip()
            if error:
                reasons.append(f"{parser}: {error}")
    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped
