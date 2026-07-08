"""Research Assistant Chat — advisory UI with human-readable HITL flow."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.assistant.probe import silent_probe
from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.intent_action import (
    ActionDecision,
    append_action_decision,
    build_research_context_snapshot,
    build_response_context_for_decision,
    has_readable_paper_artifact_content,
    infer_intent_signal,
    render_response_for_decision,
    resolve_material_auto_action,
)
from autoad_researcher.assistant.research_context_builder import (
    build_research_chat_evidence_context,
    render_research_chat_evidence_context,
)
from autoad_researcher.assistant.response_guard import guard_research_chat_reply
from autoad_researcher.core.events import EventStore
from autoad_researcher.research_context.freeze import load_active_freeze_manifest
from autoad_researcher.ui.artifact_viewer import (
    BLOCKED_REASON_HINTS,
    get_approval_gate_report,
    run_dir_path,
)
from autoad_researcher.ui.chat_client import call_research_chat
from autoad_researcher.ui.chat_prompts import MODE_PROMPTS
from autoad_researcher.ui.chat_transcript import load_transcript, save_transcript
from autoad_researcher.ui.intent_draft import (
    load_intent_confirmation,
    load_intent_draft,
    intent_draft_prompt_payload,
    parse_intent_draft_response,
    load_stage3_approval,
)
from autoad_researcher.ui.intake_bridge import (
    get_intake_bridge_status,
)
from autoad_researcher.ui.material_requests import (
    append_material_request,
    build_material_request_rows,
    load_material_requests,
)
from autoad_researcher.ui.task_profile import (
    get_task_display_info,
)
from autoad_researcher.ui.sources import (
    find_source_by_stored_path,
    find_source_entry_by_stored_path,
    get_source_context,
    list_pdf_source_entries,
    load_source_registry,
    register_url_source,
    resolve_source_pdf_path_safely,
    save_uploaded_file,
    update_source_status,
)
from autoad_researcher.ui.subagent_inbox import (
    load_uninjected_notifications,
    mark_notifications_injected,
    render_notifications_for_llm,
)
from autoad_researcher.ui.sync_web_search import (
    build_sync_web_search_reply,
)

_SAFETY_WARNING = "研究助手只提供解释和建议，不会修改代码，也不会执行真实 L3。"
_MODE_LABELS = {
    "intent_clarification": "意图澄清",
    "run_explanation": "运行解释",
    "next_experiment": "下一步建议",
}
_PAPER_PARSE_TIMEOUT_SECONDS = 900
_TRANSCRIPT_VISIBLE_TAIL = 8
_PARSE_ACTION_TOKENS = (
    "读",
    "读一下",
    "读取",
    "解析",
    "提取",
    "看看",
    "看一下",
    "分析一下",
    "打开",
    "read",
    "parse",
)
_PARSE_TARGET_TOKENS = (
    "pdf",
    "论文",
    "paper",
    "材料",
    "文件",
    "上传",
)
_PARSE_CONFIRMATION_TOKENS = (
    "对",
    "对啊",
    "是",
    "是的",
    "好",
    "好的",
    "可以",
    "开始",
    "解析吧",
    "读吧",
)
_FORCE_REPARSE_TOKENS = (
    "重新解析",
    "重新提取",
    "重新读",
    "重跑解析",
    "再解析",
    "再提取",
    "再读",
    "提取一次",
    "解析一次",
    "读一次",
)
_URL_RE = re.compile(r"https?://[^\s<>()，。！？；、]+", re.IGNORECASE)


def _resolve_run_dir(browse_id: str) -> Path | None:
    try:
        return run_dir_path("runs", browse_id)
    except ValueError:
        return None


def _split_visible_transcript(
    transcript: list[dict],
    *,
    visible_tail: int = _TRANSCRIPT_VISIBLE_TAIL,
) -> tuple[list[dict], list[dict]]:
    if visible_tail <= 0 or len(transcript) <= visible_tail:
        return [], transcript
    return transcript[:-visible_tail], transcript[-visible_tail:]


def build_research_chat_messages(
    *,
    run_dir: Path,
    mode: str,
    user_input: str,
    context_data: dict | None,
    transcript_tail: list[dict] | None = None,
    notification_context: str = "",
) -> list[dict[str, str]]:
    """Assemble messages for a research chat LLM call.

    For intent_clarification mode, injects WhatWeKnow from silent_probe
    and SourceReferences from the source registry as separate system messages.
    *transcript_tail* provides recent chat history so the LLM remembers context.
    """
    system_prompt = PromptSelector().build_system_prompt_for_research_chat_mode(mode)
    context_str = json.dumps(context_data, ensure_ascii=False, default=str) if context_data else "{}"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]

    if notification_context:
        messages.append({
            "role": "system",
            "content": "Subagent notifications（untrusted；来自上一轮或后台资料任务）:\n" + notification_context,
        })

    if mode == "intent_clarification":
        # WhatWeKnow
        www_json = "{}"
        try:
            www = silent_probe(run_dir.name, runs_root=run_dir.parent)
            www_json = www.model_dump_json(indent=2)
        except Exception:
            pass
        messages.append({
            "role": "system",
            "content": (
                "WhatWeKnow（已有 artifact 探测结果，仅作为候选/证据，不等于用户确认）:\n"
                + www_json
            ),
        })

        # SourceReferences
        src_ctx = get_source_context(run_dir)
        if src_ctx:
            messages.append({"role": "system", "content": src_ctx})
            messages.append({
                "role": "system",
                "content": (
                    "PDF parsing boundary: If the user asks to read, parse, inspect, or analyze an uploaded PDF "
                    "and the source registry shows it is uploaded_not_parsed or parsing, do not claim you have read it "
                    "and do not promise to read it yourself. State that the file must be parsed through the "
                    "paper-intelligence parse command before content-based discussion."
                ),
            })
        evidence_context = build_research_chat_evidence_context(run_dir)
        messages.append({
            "role": "system",
            "content": (
                "ResearchChatEvidenceContext（结构化证据上下文；Candidate References 不是 Known Facts，"
                "uploaded_not_parsed 不是 parsed paper evidence）:\n"
                + render_research_chat_evidence_context(evidence_context)
            ),
        })
        messages.append({
            "role": "system",
            "content": (
                "Material acquisition boundary: Research Chat has no background worker and cannot proactively "
                "send a later message after web_search/web_fetch/git_clone. If a synchronous web_search provider "
                "is available, search requests may return candidate_source_only results immediately; otherwise "
                "return search_unavailable or record material_requests for later discovery/acquisition. Never say "
                "that you will reply in 5-10 minutes."
            ),
        })
        response_ctx = build_research_chat_response_context(run_dir, transcript_tail=transcript_tail)
        messages.append({
            "role": "system",
            "content": (
                "ResponseContext（当前资料事实包；优先级高于模型记忆和旧 transcript）:\n"
                + json.dumps(response_ctx, ensure_ascii=False, indent=2)
            ),
        })
        messages.append({
            "role": "system",
            "content": (
                "Transfer recommendation boundary: When the user asks what can transfer from the parsed paper "
                "to their baseline, answer from ResponseContext.facts.paper_context and user-confirmed baseline "
                "constraints only. Do not switch to external SOTA/latest trends, do not invent numeric gains, "
                "and do not recommend changing the baseline framework unless the user explicitly permits it."
            ),
        })
        messages.append({
            "role": "system",
            "content": (
                "Confirmed chat facts boundary: Information in ResponseContext.facts.confirmed_from_chat was "
                "explicitly provided by the user in recent transcript and must not be asked again. Ask at most "
                "one genuinely blocking follow-up question. If the confirmed facts and artifacts are enough to "
                "draft a research plan, provide the plan directly. Do not treat research-level constraints as "
                "file-level patch scope."
            ),
        })

    # Transcript history (before current user_input)
    if transcript_tail:
        for entry in transcript_tail:
            role = entry.get("role", "user")
            content = entry.get("content", "")
            if role in ("user", "assistant") and content:
                # Truncate very long messages
                if len(content) > 2000:
                    content = content[:2000] + "…[truncated]"
                messages.append({"role": role, "content": content})

    messages.append({"role": "system", "content": "当前运行上下文:\n" + context_str})
    messages.append({"role": "user", "content": user_input})
    return messages


def build_research_chat_response_context(
    run_dir: Path,
    *,
    transcript_tail: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshot = build_research_context_snapshot(run_dir)
    decision = ActionDecision(
        snapshot_sha256="research_chat",
        selected_action="answer_directly",
        response_mode="answer_directly",
        reason="research chat evidence package",
    )
    return _sanitize_response_context_for_llm(
        build_response_context_for_decision(snapshot, decision, transcript_tail=transcript_tail)
    )


def _run_paper_intelligence(run_id: str, pdf_path: Path) -> dict[str, str]:
    """Trigger paper-intelligence CLI for a PDF.

    UI does NOT call MinerU directly — it delegates to the CLI.
    Returns {"status": "parsed"} or {"status": "failed", "error": "..."}.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [
                "uv", "run", "autoad", "paper-intelligence",
                "--run-id", run_id,
                "--pdf", str(pdf_path),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=_PAPER_PARSE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "error": "解析超时（15 分钟）"}
    except FileNotFoundError:
        return {"status": "failed", "error": "autoad CLI 未找到"}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)[:200]}

    if proc.returncode == 0:
        return {"status": "parsed"}
    try:
        import json
        out = json.loads(proc.stdout)
        parts = []
        if (s := out.get("status")):
            parts.append(f"status={s}")
        if (s := out.get("stage")):
            parts.append(f"stage={s}")
        if (s := out.get("error")):
            parts.append(f"error={s}")
        if (ws := out.get("warnings")):
            parts.append(f"warnings={ws[:2]}")
        if (e := out.get("post_validation_errors")):
            parts.append(f"post_validation_errors={e}")
        err = "; ".join(parts)
    except Exception:
        err = proc.stderr.strip()[:200] or f"returncode={proc.returncode}"
    return {"status": "failed", "error": err}


def normalize_chat_submission(submission: Any) -> tuple[str, list[Any]]:
    """Normalize Streamlit chat_input return values across versions.

    Streamlit 1.58 returns a ChatInputValue when files are accepted, while older
    code paths and tests may still pass a plain string.
    """
    if submission is None:
        return "", []
    if isinstance(submission, str):
        return submission.strip(), []
    if isinstance(submission, dict):
        text = str(submission.get("text") or submission.get("message") or "").strip()
        files = submission.get("files") or []
        return text, list(files)

    text_value = getattr(submission, "text", None)
    if text_value is None:
        text_value = getattr(submission, "message", "")
    files_value = getattr(submission, "files", []) or []
    return str(text_value or "").strip(), list(files_value)


def save_chat_attachments(run_dir: Path, uploaded_files: list[Any]) -> list[dict[str, Any]]:
    """Save chat-input attachments to the source registry."""
    saved: list[dict[str, Any]] = []
    for uploaded_file in uploaded_files:
        info = save_uploaded_file(run_dir, uploaded_file)
        saved.append(info)
    return saved


def build_attachment_added_reply(sources: list[dict[str, Any]]) -> str:
    names = [Path(str(source["stored_path"])).name for source in sources]
    lines = [
        "已添加资料：",
        *[f"- {name}" for name in names],
        "",
        "下一步可以说：读一下这个论文",
    ]
    return "\n".join(lines)


def _attachment_user_message(sources: list[dict[str, Any]]) -> str:
    names = [Path(str(source["stored_path"])).name for source in sources]
    return "上传资料：" + "、".join(names)


def extract_first_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,;:!?)]}")


def create_url_source_material_request(run_dir: Path, url: str, *, user_message: str | None = None) -> dict[str, Any]:
    source = register_url_source(run_dir, url)
    source_id = str(source["source_id"])
    source_kind = str(source.get("kind", "webpage"))
    existing = _find_existing_material_request_for_url(run_dir, url=url, source_id=source_id)
    if existing is not None:
        return {"source": source, "request": existing, "existing_request": True}
    if source_kind == "github_repo":
        request = append_material_request(
            run_dir,
            user_message=user_message or url,
            kind="repository_discovery",
            payload={"url": url, "source_id": source_id},
            evidence_role="candidate_source_only",
        )
    else:
        request = append_material_request(
            run_dir,
            user_message=user_message or url,
            kind="material_acquisition",
            payload={"tool": "web_fetch", "url": url, "source_id": source_id},
            evidence_role="source_acquired_unparsed",
        )
    return {"source": source, "request": request}


def _find_existing_material_request_for_url(run_dir: Path, *, url: str, source_id: str) -> dict[str, Any] | None:
    for request in reversed(load_material_requests(run_dir)):
        status = str(request.get("status", ""))
        if status in {"failed", "cancelled"}:
            continue
        payload = request.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("url") == url and payload.get("source_id") == source_id:
            return request
    return None


def build_url_source_material_request_reply(intake: dict[str, Any], runs: list[dict[str, Any]] | None = None) -> str:
    source = intake["source"]
    request = intake["request"]
    source_id = str(source.get("source_id", "source"))
    source_kind = str(source.get("kind", "url"))
    request_id = str(request.get("request_id", "material_request"))
    request_kind = str(request.get("kind", "material_acquisition"))
    evidence_role = str(request.get("evidence_role", "source_acquired_unparsed"))
    if intake.get("existing_request"):
        base = (
            f"已复用 URL source：`{source_id}`（{source_kind}）。\n"
            f"已有资料处理任务 `{request_id}`（{request_kind}，{evidence_role}），不会重复创建。"
        )
    else:
        base = (
            f"已登记 URL source：`{source_id}`（{source_kind}）。\n"
            f"同时已创建资料处理任务 `{request_id}`（{request_kind}，{evidence_role}）。"
        )
    if runs:
        base += "\n已自动执行资料处理任务；结果已写入通知区。任务状态见「资料搜集请求」面板。"
    else:
        base += "\n任务完成后会生成网页、论文或仓库候选 artifact；在获取或解析完成前，还不是 supported facts。"
    return base


def create_search_unavailable_material_request(
    run_dir: Path,
    *,
    user_input: str,
    search_result: dict[str, Any],
) -> dict[str, Any]:
    reason = str(search_result.get("reason", "web_search provider is not configured"))
    return append_material_request(
        run_dir,
        user_message=user_input,
        kind="web_search",
        payload={"query": user_input, "unavailable_reason": reason},
        evidence_role="candidate_source_only",
    )


def build_search_unavailable_material_request_reply(search_result: dict[str, Any], request: dict[str, Any], runs: list[dict[str, Any]] | None = None) -> str:
    request_id = str(request.get("request_id", "material_request"))
    prefix = build_sync_web_search_reply(search_result)
    if runs:
        return (
            prefix + "\n"
            + f"已创建并自动执行资料搜集任务 `{request_id}`。结果见通知区。"
        )
    return (
        prefix + "\n"
        + f"我已创建资料搜集任务 `{request_id}`。你可以在「资料搜集请求」面板手动触发，或由 worker 处理；"
        "完成后结果会写入通知区，我会在你刷新或继续对话时读取。"
    )


def detect_parse_intent(user_input: str) -> bool:
    """Return True when user text asks to parse/read uploaded paper material."""
    text = user_input.strip()
    if not text:
        return False
    if _is_force_reparse_intent(text):
        return True
    if re.search(r"sources/((?:[^/\s]+/)*[^/\s]+\.pdf)", text, re.IGNORECASE):
        return True
    lowered = text.lower()
    has_action = _has_parse_action(lowered)
    has_target = _has_parse_target(lowered)
    return has_action and has_target


def _has_parse_action(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in _PARSE_ACTION_TOKENS)


def _has_parse_target(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in _PARSE_TARGET_TOKENS)


def _is_short_parse_confirmation(text: str) -> bool:
    normalized = re.sub(r"[\s。！!？?，,；;：:]+", "", text.strip().lower())
    return normalized in _PARSE_CONFIRMATION_TOKENS


def _is_force_reparse_intent(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.strip().lower())
    return any(token in normalized for token in _FORCE_REPARSE_TOKENS)


def build_pdf_parse_action(
    run_dir: Path,
    user_input: str,
    *,
    recent_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve a user message into a material action through the v1.4 adapter."""
    explicit_path = resolve_source_pdf_path_safely(run_dir, user_input)
    explicit_stored_path = explicit_path.relative_to(run_dir).as_posix() if explicit_path is not None else None

    snapshot = build_research_context_snapshot(run_dir)
    signal = infer_intent_signal(user_input, snapshot)
    decision = resolve_material_auto_action(
        snapshot=snapshot,
        signal=signal,
        explicit_stored_path=explicit_stored_path,
        recent_sources=recent_sources,
    )

    if explicit_path is not None:
        return _legacy_action_from_decision(run_dir, decision, pdf_path=explicit_path)

    if re.search(r"sources/[^ \t\r\n]+\.pdf", user_input, re.IGNORECASE):
        return {
            "action": "missing",
            "message": "没有找到这个 PDF 路径。请检查 `sources/...pdf` 是否和 SourceReferences 中的 path 完全一致。",
            "action_decision": decision,
        }

    if (
        decision.selected_action == "answer_directly"
        and decision.response_mode == "empty_run_intake"
        and not signal.mentions_paper
        and not signal.mentions_repo
        and not signal.requests_execution
        and not signal.confirms_research_task
    ):
        return {"action": "chat"}

    return _legacy_action_from_decision(run_dir, decision)


def _legacy_action_from_decision(run_dir: Path, decision: ActionDecision, *, pdf_path: Path | None = None) -> dict[str, Any]:
    """Convert the v1.4 ActionDecision to the pre-existing UI action shape."""
    base: dict[str, Any] = {"action_decision": decision}
    if decision.selected_action == "parse_uploaded_pdf":
        if not decision.stored_path:
            return {**base, "action": "missing", "message": "当前没有可解析的 PDF。请先上传 PDF。"}
        resolved_pdf = pdf_path or (run_dir / decision.stored_path)
        if not resolved_pdf.is_file():
            return {
                **base,
                "action": "missing",
                "message": "没有找到这个 PDF 文件，请重新上传或检查 SourceReferences。",
            }
        return {
            **base,
            "action": "parse",
            "source_id": decision.source_id,
            "stored_path": decision.stored_path,
            "pdf_path": resolved_pdf,
        }
    if decision.response_mode == "select_pdf_to_parse":
        return {**base, "action": "choose", "message": render_response_for_decision(build_research_context_snapshot(run_dir), decision)}
    if decision.response_mode == "uploaded_not_parsed_status" and not decision.stored_path:
        return {**base, "action": "missing", "message": render_response_for_decision(build_research_context_snapshot(run_dir), decision)}
    if decision.response_mode == "parsing_in_progress_status":
        return {**base, "action": "already_parsing", "message": render_response_for_decision(build_research_context_snapshot(run_dir), decision)}
    if decision.response_mode in {"parsed_artifact_summary", "parsed_artifact_insufficient"}:
        return {**base, "action": "already_parsed", "message": render_response_for_decision(build_research_context_snapshot(run_dir), decision)}
    if decision.response_mode == "parsing_failed_status":
        return {**base, "action": "parse_failed", "message": render_response_for_decision(build_research_context_snapshot(run_dir), decision)}
    if decision.response_mode == "execution_request_blocked":
        return {**base, "action": "blocked", "message": render_response_for_decision(build_research_context_snapshot(run_dir), decision)}
    return {**base, "action": "message", "message": render_response_for_decision(build_research_context_snapshot(run_dir), decision)}


def _pdf_parse_action_for_source(
    run_dir: Path,
    *,
    stored_path: str,
    entry: dict[str, Any] | None,
    pdf_path: Path,
    force: bool = False,
) -> dict[str, Any]:
    status = entry.get("status") if entry else "uploaded_not_parsed"
    if status == "parsing":
        return {
            "action": "already_parsing",
            "message": f"`{stored_path}` 正在解析中，请等待完成后再继续。",
        }
    if status == "parsed" and not force:
        return {
            "action": "already_parsed",
            "message": f"`{stored_path}` 已解析，可直接基于已生成的 paper artifacts 讨论。",
        }
    if not pdf_path.is_file():
        return {
            "action": "missing",
            "message": f"没有找到 `{stored_path}` 对应的 PDF 文件，请重新上传或检查 SourceReferences。",
        }
    return {
        "action": "parse",
        "source_id": entry.get("source_id") if entry else find_source_by_stored_path(run_dir, stored_path),
        "stored_path": stored_path,
        "pdf_path": pdf_path,
    }


_RESEARCH_CHAT_ALLOWED_ACTIONS = {
    "parse",
    "missing",
    "choose",
    "already_parsing",
    "already_parsed",
    "parse_failed",
    "blocked",
    "message",
}


def _execute_or_report_pdf_parse_action(
    run_dir: Path, action: dict[str, Any],
    *, api_key: str = "", provider_url: str = "", user_input: str = "",
) -> str:
    kind = str(action.get("action", ""))
    if kind not in _RESEARCH_CHAT_ALLOWED_ACTIONS:
        return _reject_research_chat_action(run_dir, kind, action)
    decision: ActionDecision | None = action.get("action_decision")
    if kind == "parse":
        pdf_path = Path(action["pdf_path"])
        source_id = action.get("source_id")
        if decision is not None:
            append_action_decision(run_dir, decision.model_copy(update={"execution_status": "planned"}))

        force_reparse = any(
            kw in str(user_input).lower()
            for kw in ("强制重新解析", "强制解析", "重新解析", "reparse", "force reparse")
        )
        if source_id and not force_reparse:
            registry = load_source_registry(run_dir)
            for src in registry.get("sources", []):
                if isinstance(src, dict) and src.get("source_id") == source_id:
                    if src.get("status") == "failed":
                        prev_err = src.get("error_message", "")
                        if prev_err:
                            return (
                                "该 PDF 上次解析已经失败，错误相同；我没有重复触发解析。"
                                "需要强制重试请说：强制重新解析。"
                            )
                    break

        if source_id:
            update_source_status(run_dir, str(source_id), "parsing")
        result = _run_paper_intelligence(run_dir.name, pdf_path)
        if result["status"] == "parsed":
            refreshed = build_research_context_snapshot(run_dir)
            has_readable_content = has_readable_paper_artifact_content(run_dir)
            if refreshed.paper_artifact_quality != "usable" and not has_readable_content:
                err = "paper artifacts 质量不足，不能基于论文正文回答"
                if source_id:
                    update_source_status(run_dir, str(source_id), "failed", error_message=err)
                final_decision = _parse_result_decision(
                    decision,
                    execution_status="executed_failed",
                    response_mode="parsed_artifact_insufficient",
                    source_status_after="parsing_failed",
                    error_code="PAPER_ARTIFACTS_INSUFFICIENT",
                    user_visible_error=err,
                    fallback_message=f"⚠️ {pdf_path.name} 解析流程已完成，但生成的 paper artifacts 证据不足。当前不能基于论文正文作可靠判断。",
                )
                if final_decision is not None:
                    append_action_decision(run_dir, final_decision)
                reply = _natural_or_fallback_parse_reply(
                    run_dir=run_dir,
                    decision=final_decision,
                    api_key=api_key,
                    provider_url=provider_url,
                    user_input=user_input,
                    fallback=f"⚠️ {pdf_path.name} 解析流程已完成，但生成的 paper artifacts 证据不足。当前不能基于论文正文作可靠判断。",
                )
                return reply
            if source_id:
                update_source_status(run_dir, str(source_id), "parsed")
            if refreshed.paper_artifact_quality != "usable":
                fallback_message = (
                    f"✅ {pdf_path.name} 解析已完成，已生成可读取的 paper artifacts。"
                    "metadata 不完整，我会在后续回答里标注这些限制。"
                )
                response_mode = "parsed_artifact_insufficient"
                error_code = "PAPER_ARTIFACTS_PARTIAL_METADATA"
            else:
                reply_parts = [f"✅ {pdf_path.name} 已完成 paper-intelligence 解析。"]
                if refreshed.paper_methods:
                    methods = "；".join(refreshed.paper_methods[:5])
                    reply_parts.append(f"我从 artifacts 看到：{methods}")
                if refreshed.missing_blocking_gaps:
                    gaps = "、".join(refreshed.missing_blocking_gaps[:5])
                    reply_parts.append(f"仍缺：{gaps}")
                reply_parts.append("后续回答将只基于可用 paper artifacts。")
                fallback_message = "\n\n".join(reply_parts)
                response_mode = "parsed_artifact_summary"
                error_code = None
            final_decision = _parse_result_decision(
                decision,
                execution_status="executed_success",
                response_mode=response_mode,
                source_status_after="parsed",
                error_code=error_code,
                fallback_message=fallback_message,
            )
            if final_decision is not None:
                append_action_decision(run_dir, final_decision)
            reply = _natural_or_fallback_parse_reply(
                run_dir=run_dir,
                decision=final_decision,
                api_key=api_key,
                provider_url=provider_url,
                user_input=user_input,
                fallback=fallback_message,
            )
            return reply
        err = result.get("error", "未知错误")
        if source_id:
            update_source_status(run_dir, str(source_id), "failed", error_message=err)
        fallback_message = f"❌ {pdf_path.name} 解析失败：{err}"
        final_decision = _parse_result_decision(
            decision,
            execution_status="executed_failed",
            response_mode="parsing_failed_status",
            source_status_after="parsing_failed",
            error_code="PAPER_PARSE_FAILED",
            user_visible_error="PDF 解析未成功，请检查文件是否完整",
            fallback_message=fallback_message,
        )
        if final_decision is not None:
            append_action_decision(run_dir, final_decision)
        reply = _natural_or_fallback_parse_reply(
            run_dir=run_dir,
            decision=final_decision,
            api_key=api_key,
            provider_url=provider_url,
            user_input=user_input,
            fallback=fallback_message,
        )
        return reply

    message = str(action["message"])
    # For non-parse actions with a user-visible message, try LLM natural reply first
    if decision is not None:
        append_action_decision(run_dir, decision)
    if api_key and user_input and kind in {"message", "already_parsed", "already_parsing", "parse_failed", "missing", "choose", "blocked"}:
        natural = _natural_reply_for_decision(
            run_dir=run_dir, decision=decision, api_key=api_key,
            provider_url=provider_url, user_input=user_input,
        )
        return natural

    return message


def _sanitize_response_context_for_llm(ctx: dict[str, Any]) -> dict[str, Any]:
    """Remove internal placeholder values before sending to LLM."""
    clean = json.loads(json.dumps(ctx, default=str))
    facts = clean.get("facts", {})
    if isinstance(facts, dict):
        attempts = facts.get("available_parse_attempts") or facts.get("parse_attempts") or []
        if isinstance(attempts, list):
            for a in attempts:
                if isinstance(a, dict) and a.get("parser") == "unknown_legacy":
                    a.pop("parser", None)
                    a.pop("legacy_parse_attempt", None)
    return clean


def _parse_result_decision(
    decision: ActionDecision | None,
    *,
    execution_status: str,
    response_mode: str,
    source_status_after: str,
    fallback_message: str,
    error_code: str | None = None,
    user_visible_error: str | None = None,
) -> ActionDecision | None:
    if decision is None:
        return None
    return decision.model_copy(update={
        "selected_action": "summarize_parsed_artifacts",
        "response_mode": response_mode,
        "execution_status": execution_status,
        "source_status_after": source_status_after,
        "error_code": error_code,
        "user_visible_message": fallback_message,
        "user_visible_error": user_visible_error,
    })


def _natural_or_fallback_parse_reply(
    *,
    run_dir: Path,
    decision: ActionDecision | None,
    api_key: str,
    provider_url: str,
    user_input: str,
    fallback: str,
) -> str:
    if decision is not None and api_key and user_input:
        return _natural_reply_for_decision(
            run_dir=run_dir,
            decision=decision,
            api_key=api_key,
            provider_url=provider_url,
            user_input=user_input,
        )
    if decision is not None:
        return render_response_for_decision(build_research_context_snapshot(run_dir), decision)
    return fallback


def _natural_reply_for_decision(
    *,
    run_dir: Path,
    decision: ActionDecision,
    api_key: str,
    provider_url: str,
    user_input: str,
    history_tail: list[dict[str, str]] | None = None,
) -> str:
    """Use LLM to generate a natural response from the ResponseContext.

    Falls back to the deterministic template if the LLM is unavailable.
    """
    snapshot = build_research_context_snapshot(run_dir)
    if history_tail is None:
        try:
            transcript = load_transcript(run_dir)
            history_tail = [
                {"role": entry.get("role", "user"), "content": str(entry.get("content", ""))[:1200]}
                for entry in transcript[-6:]
                if entry.get("role") in {"user", "assistant"} and entry.get("content")
            ]
        except Exception:
            history_tail = []
    response_ctx = _sanitize_response_context_for_llm(
        build_response_context_for_decision(snapshot, decision, transcript_tail=history_tail)
    )

    try:
        system = (
            PromptSelector().build_system_prompt_for_research_chat_mode("intent_clarification")
            + '\n\n'
            '回复不超过 4 行，除非用户明确要求详细说明。'
            '不要以你好开头。'
            '不要重复已经在最近对话中说过的诊断、背景和排查过程。'
            '优先给当前结论和下一步命令。'
            '可以登记 GitHub 仓库链接作为 source，后续实验 agents 可以 clone 并分析；这不等于当前聊天会执行 runner、patch 或 benchmark。'
            '优先使用 ResponseContext.facts.paper_context；若 paper_context.can_answer_from_paper 为 true，必须基于 paper_context、paper.md、paper_summary.json 或 sections.json 自然回答论文内容，不要只说“解析成功”。'
            '如果 paper_context.can_answer_from_paper 为 false，不得假装读过论文；应说明当前可用 artifact 仍没有可读论文文本。'
            'ResponseContext 优先级高于 transcript；如果旧对话说“无法读取”但当前 paper_context 可回答，应把旧结论视为过期并更正。'
            '读取论文内容时优先从 paper.md 和 paper_summary.json 取内容；blocks.jsonl 的 page 1 可能包含 PDF 二进制块，应跳过乱码块，不要因为 blocks.jsonl 局部乱码否定其它可读 artifact。'
            '不要反复要求用户提供链接；不要声称系统没有 web_search、web_fetch 或 git_clone ToolSpec。当前聊天未触发 acquisition 时，只说明可登记为待获取 source 或交给后续 discovery/acquisition agents。'
            '不得声称读过未解析资料，不得承诺执行 patch、runner、benchmark 或真实实验。'
        )
        if decision.response_mode == "research_task_confirmed":
            system += (
                '当前 response_mode 是 research_task_confirmed；不要输出固定表单或只说“已确认”。'
                '请给自然语言研究方案，包含：已确认事实、研究目标、候选方向、评估计划、执行边界、下一步。'
                'Scope 只表达功能级研究约束；不要列具体文件路径、模块路径或 patch hook。'
            )
        ctx_text = json.dumps(response_ctx, ensure_ascii=False, indent=2)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "system", "content": f"ResponseContext:\n{ctx_text}"},
        ]
        history_tail = _filter_stale_paper_history(history_tail, response_ctx)
        if history_tail:
            messages.extend(history_tail)
        messages.append({"role": "user", "content": user_input})

        result = call_research_chat(
            api_key=api_key,
            provider_base_url=provider_url,
            messages=messages,
        )
        if result.get("reply") and not result.get("error"):
            return result["reply"]
    except Exception:
        pass

    return render_response_for_decision(snapshot, decision)


def _filter_stale_paper_history(history_tail: list[dict[str, str]], response_ctx: dict[str, Any]) -> list[dict[str, str]]:
    facts = response_ctx.get("facts", {})
    paper_context = facts.get("paper_context", {}) if isinstance(facts, dict) else {}
    if not (isinstance(paper_context, dict) and paper_context.get("can_answer_from_paper") is True):
        return history_tail
    filtered: list[dict[str, str]] = []
    for entry in history_tail:
        if entry.get("role") == "assistant" and _is_stale_unreadable_paper_reply(entry.get("content", "")):
            continue
        filtered.append(entry)
    return filtered


def _is_stale_unreadable_paper_reply(content: str) -> bool:
    text = str(content)
    stale_tokens = (
        "无法读取",
        "不可读",
        "无法提取论文内容",
        "无法提取出可读",
        "没有可读正文",
        "正文块仍为乱码",
        "乱码",
        "arXiv HTML",
        "可复制文本的 PDF",
        "扫描版",
        "编码格式异常",
        "mineru 解析器无法",
        "MinerU 解析器无法",
    )
    return any(token in text for token in stale_tokens)


def _reject_research_chat_action(run_dir: Path, kind: str, action: dict[str, Any]) -> str:
    message = "Research Assistant 工具隔离已拒绝非白名单动作；不会启动 patch、runner、benchmark 或实验执行。"
    EventStore(runs_root=run_dir.parent).append(
        run_dir.name,
        "tool_guard_rejected",
        {
            "stage": "research_chat",
            "action": kind,
            "reason": "action not allowed in research chat parse execution boundary",
            "allowed_actions": sorted(_RESEARCH_CHAT_ALLOWED_ACTIONS),
            "requested_keys": sorted(str(key) for key in action.keys()),
        },
    )
    return message


def _save_assistant_reply_and_mark_notifications(
    run_dir: Path,
    mode: str,
    reply: str,
    *,
    context_refs: list[str] | None = None,
    notifications: list[dict[str, Any]] | None = None,
) -> None:
    save_transcript(run_dir, mode, "assistant", reply, context_refs=context_refs)
    if notifications:
        reply_id = f"reply_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
        mark_notifications_injected(run_dir, notifications, reply_id=reply_id)


# ---------------------------------------------------------------------------
# User-facing panels
# ---------------------------------------------------------------------------


def build_source_card_rows(run_dir: Path) -> list[dict[str, Any]]:
    registry = load_source_registry(run_dir)
    rows: list[dict[str, Any]] = []
    for source in registry.get("sources", []):
        if not isinstance(source, dict):
            continue
        attempts = source.get("parse_attempts", [])
        attempts = attempts if isinstance(attempts, list) else []
        active = source.get("active_parse_attempt_id")
        rows.append({
            "source_id": source.get("source_id"),
            "kind": source.get("kind"),
            "label": source.get("user_label"),
            "status": source.get("status"),
            "intake_status": source.get("intake_status"),
            "stored_path": source.get("stored_path"),
            "active_parse_attempt_id": active,
            "parse_attempt_count": len([item for item in attempts if isinstance(item, dict)]),
            "attempts": [
                {
                    "parse_attempt_id": attempt.get("parse_attempt_id"),
                    "status": attempt.get("status"),
                    "parser": attempt.get("parser"),
                    "quality_report": attempt.get("quality_report"),
                    "active": bool(active and attempt.get("parse_attempt_id") == active),
                }
                for attempt in attempts
                if isinstance(attempt, dict)
            ],
        })
    return rows


def build_freeze_panel_state(run_dir: Path) -> dict[str, Any]:
    manifest = load_active_freeze_manifest(run_dir)
    draft_exists = (run_dir / "context" / "research_context_draft.json").is_file()
    active = manifest.get("active_freeze_version") if isinstance(manifest, dict) else None
    freezes = manifest.get("freezes") if isinstance(manifest, dict) else []
    return {
        "draft_exists": draft_exists,
        "active_freeze_version": active if isinstance(active, str) else None,
        "freezes": freezes if isinstance(freezes, list) else [],
        "button_enabled": draft_exists,
    }


# ---------------------------------------------------------------------------
# Developer info and pure helpers
# ---------------------------------------------------------------------------


def build_research_assistant_overview(
    run_dir: Path,
    *,
    dataset_root: str,
    provider_url: str,
    context_data: dict | None,
) -> dict[str, Any]:
    display = get_task_display_info(run_dir)
    available_stages = []
    if isinstance(context_data, dict):
        raw_stages = context_data.get("available_stages", [])
        if isinstance(raw_stages, list):
            available_stages = [str(stage) for stage in raw_stages]
    return {
        "task_title": display["task_title"],
        "task_summary": display["task_summary"],
        "status_label": determine_task_status_label(run_dir),
        "dataset_status": "已配置" if str(dataset_root).strip() else "未配置",
        "developer": {
            "run_id": display["run_id"],
            "artifact_dir": display["artifact_dir"],
            "provider": provider_url,
            "dataset_root": dataset_root,
            "available_stages": available_stages,
            "raw_artifacts": [
                "ui_chat/intent_draft.json",
                "ui_chat/clarification_input.json",
                "approvals/intent_confirmation.json",
                "approvals/patch_approval.json",
                "approvals/run_approval.json",
                "approval_gate_report.json",
                "ui_chat/research_context_snapshot.json",
                "ui_chat/action_decisions.jsonl",
            ],
        },
    }


def determine_task_status_label(run_dir: Path) -> str:
    if (run_dir / "final_report" / "final_report_facts.json").is_file():
        return "可查看最终报告"
    confirmation = load_intent_confirmation(run_dir)
    if not confirmation or confirmation.decision != "approved":
        return "正在确认研究目标"
    if not (run_dir / "input_task.yaml").is_file():
        return "等待生成实验输入"
    patch_approval = load_stage3_approval(run_dir, decision_type="patch_approval")
    if not patch_approval or not patch_approval.confirmed_by_user:
        return "等待审批代码修改"
    run_approval = load_stage3_approval(run_dir, decision_type="run_approval")
    if not run_approval or not run_approval.confirmed_by_user:
        return "等待审批真实执行"
    return "可查看最终报告"


def render_intent_draft_markdown(draft: Any) -> str:
    return "\n".join([
        "**目标**",
        str(draft.research_goal),
        "",
        "**评价指标**",
        *_metric_bullets(draft.primary_metrics),
        *_section_bullets("底线指标", draft.guardrail_metrics),
        *_section_bullets("允许修改", draft.allowed_change_scope),
        *_section_bullets("禁止修改", draft.forbidden_change_scope),
        "**验收标准**",
        str(draft.success_criteria),
    ])


def _metric_bullets(metrics: list[str]) -> list[str]:
    if not metrics:
        return ["- 暂未指定"]
    return [f"- {_metric_label(metric)}：{metric}" for metric in metrics]


def _metric_label(metric: str) -> str:
    mapping = {
        "instance_auroc": "图像级 AUROC",
        "full_pixel_auroc": "像素级 AUROC",
        "anomaly_pixel_auroc": "异常区域像素 AUROC",
        "wall_time_seconds": "运行时间",
        "peak_gpu_memory_mb": "峰值显存",
    }
    return mapping.get(metric, "指标")


def _section_bullets(title: str, values: list[str]) -> list[str]:
    bullets = values or ["暂未指定"]
    return ["", f"**{title}**", *[f"- {value}" for value in bullets]]


def build_pipeline_input_action(run_dir: Path) -> dict[str, Any]:
    status = get_intake_bridge_status(run_dir)
    if not status["intent_confirmation_exists"] or status["intent_confirmation_decision"] != "approved":
        return {
            "message": "请先确认研究目标。确认后，系统会准备后续实验输入。",
            "button_enabled": False,
        }
    if status["input_task_exists"]:
        return {
            "message": "实验输入已准备好。后续 pipeline 到达相应阶段后，会请求你审批代码修改方案。",
            "button_enabled": False,
        }
    return {
        "message": "研究目标已确认。下一步可以生成实验输入。",
        "button_enabled": True,
    }


def build_user_flow_steps(run_dir: Path) -> list[dict[str, Any]]:
    status = determine_task_status_label(run_dir)
    labels = [
        "确认研究目标",
        "生成实验输入",
        "审批代码修改方案",
        "审批真实执行",
        "查看最终报告",
    ]
    current_index = {
        "正在确认研究目标": 1,
        "等待生成实验输入": 2,
        "等待审批代码修改": 3,
        "等待审批真实执行": 4,
        "可查看最终报告": 5,
    }[status]
    return [
        {
            "index": idx,
            "label": label,
            "state": "done" if idx < current_index else "current" if idx == current_index else "pending",
        }
        for idx, label in enumerate(labels, 1)
    ]


def build_hitl_gate_status_rows(run_dir: Path) -> list[dict[str, str]]:
    rows = []
    for stage, gate in [
        ("patch_planner", "intent_confirmation"),
        ("patch_applicator", "patch_approval"),
        ("runner_execute", "run_approval"),
    ]:
        report = get_approval_gate_report(run_dir, stage)
        if report:
            blocked_reason = str(report.get("blocked_reason") or "")
            next_action = BLOCKED_REASON_HINTS.get(blocked_reason, "查看 approval gate report。")
            rows.append({
                "stage": stage,
                "gate": gate,
                "status": str(report.get("status", "unknown")),
                "required_artifact": str(report.get("required_artifact", "")),
                "decision": str(report.get("decision") or ""),
                "next_action": "已通过。" if report.get("status") == "passed" else next_action,
            })
        else:
            rows.append({
                "stage": stage,
                "gate": gate,
                "status": "not_checked",
                "required_artifact": _required_gate_artifact(gate),
                "decision": "",
                "next_action": "运行 pipeline 到该阶段后会生成 gate report。",
            })
    return rows


def build_developer_info_payload(
    run_dir: Path,
    *,
    overview: dict[str, Any],
    provider_url: str,
    dataset_root: str,
    context_data: dict | None,
) -> dict[str, Any]:
    return {
        **overview["developer"],
        "provider": provider_url,
        "dataset_root": dataset_root,
        "artifact_dir": str(run_dir),
        "approval_gate_status": build_hitl_gate_status_rows(run_dir),
        "llm_context_available": context_data is not None,
        "action_decisions": str(run_dir / "ui_chat" / "action_decisions.jsonl"),
    }


def _required_gate_artifact(gate: str) -> str:
    return {
        "intent_confirmation": "approvals/intent_confirmation.json",
        "patch_approval": "approvals/patch_approval.json",
        "run_approval": "approvals/run_approval.json",
    }[gate]


# ── Legacy display-only extraction kept for regression and backward compatibility.

def _extract_intent_draft(text: str) -> dict:
    """Best-effort extraction of structured intent from an LLM reply.

    This legacy helper is display-only and never writes pipeline artifacts.
    Phase 2B production flow uses ``ResearchIntentDraft`` in intent_draft.py.
    """
    draft = {
        "research_goal": "",
        "primary_metrics": [],
        "guardrail_metrics": [],
        "allowed_change_scope": [],
        "forbidden_change_scope": [],
        "success_criteria": "",
        "constraints": [],
        "user_idea": "",
    }

    def _lines_after(text: str, *triggers: str) -> list[str]:
        for trigger in triggers:
            m = re.search(rf"{trigger}[：:]?\s*\n*(.{{0,500}})", text, re.IGNORECASE | re.DOTALL)
            if m:
                return [l.strip("-*• ") for l in m.group(1).strip().splitlines() if l.strip()]
        return []

    def _first_line_after(text: str, *triggers: str) -> str:
        lines = _lines_after(text, *triggers)
        return lines[0] if lines else ""

    draft["research_goal"] = _first_line_after(text, "研究目标", "核心目标", "research.goal")
    draft["primary_metrics"] = _lines_after(text, "优化指标", "主要指标", "primary.metric")
    draft["guardrail_metrics"] = _lines_after(text, "底线指标", "保护指标", "guardrail.metric")
    draft["allowed_change_scope"] = _lines_after(text, "允许修改", "可修改", "allowed.change")
    draft["forbidden_change_scope"] = _lines_after(text, "禁止修改", "不可修改", "forbidden.change")
    draft["success_criteria"] = _first_line_after(text, "验收标准", "成功标准", "success.criteria")
    draft["constraints"] = _lines_after(text, "约束", "限制", "constraint")
    draft["user_idea"] = _first_line_after(text, "实验想法", "原始想法", "user.idea")

    return draft
