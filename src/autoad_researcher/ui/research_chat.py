"""Research Assistant Chat — advisory UI with human-readable HITL flow."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    import streamlit as st
except ModuleNotFoundError:  # UI extra is optional in CI/unit-test environments.
    st = None

from autoad_researcher.assistant.probe import silent_probe
from autoad_researcher.assistant.intent_action import (
    ActionDecision,
    append_action_decision,
    build_research_context_snapshot,
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
from autoad_researcher.research_context.freeze import freeze_context, load_active_freeze_manifest
from autoad_researcher.ui.artifact_viewer import (
    BLOCKED_REASON_HINTS,
    get_approval_gate_report,
    run_dir_path,
)
from autoad_researcher.ui.chat_client import call_research_chat
from autoad_researcher.ui.chat_context import build_chat_context
from autoad_researcher.ui.chat_prompts import MODE_PROMPTS
from autoad_researcher.ui.chat_transcript import load_transcript, save_transcript
from autoad_researcher.ui.intent_draft import (
    load_intent_confirmation,
    load_intent_draft,
    intent_draft_prompt_payload,
    parse_intent_draft_response,
    save_clarification_input,
    save_intent_confirmation,
    save_intent_draft,
    load_stage3_approval,
    save_stage3_approval,
)
from autoad_researcher.ui.intake_bridge import (
    get_intake_bridge_status,
    save_input_task_yaml_from_clarification,
)
from autoad_researcher.ui.task_profile import (
    generate_task_profile_from_first_message,
    get_task_display_info,
    safe_load_task_profile,
    save_task_profile,
)
from autoad_researcher.ui.sources import (
    find_source_by_stored_path,
    find_source_entry_by_stored_path,
    get_source_context,
    list_pdf_source_entries,
    load_source_registry,
    register_local_file_source,
    resolve_source_pdf_path_safely,
    save_uploaded_file,
    set_active_parse_attempt,
    update_source_status,
)

_SAFETY_WARNING = "研究助手只提供解释和建议，不会修改代码，也不会执行真实 L3。"
_MODE_LABELS = {
    "intent_clarification": "意图澄清",
    "run_explanation": "运行解释",
    "next_experiment": "下一步建议",
}
_PAPER_PARSE_TIMEOUT_SECONDS = 900
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


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------


def render_research_chat():
    if st is None:
        raise RuntimeError("streamlit is required to render the Research Assistant UI")
    st.title("研究助手")
    st.caption(_SAFETY_WARNING)

    api_key = st.session_state.get("_api_key_raw")
    provider_url = st.session_state.get("provider_base_url", "https://api.deepseek.com")
    dataset_root = st.session_state.get("dataset_root", "")
    browse_id = st.session_state.get("_browse_run_id", st.session_state.get("_run_id_hash", ""))

    run_dir = _resolve_run_dir(browse_id)
    context_data = build_chat_context(run_dir) if run_dir and run_dir.is_dir() else None

    if run_dir is None:
        st.info("请先在侧边栏选择一个任务，或在「运行配置」中创建新任务。")
        return

    overview = build_research_assistant_overview(
        run_dir,
        dataset_root=dataset_root,
        provider_url=provider_url,
        context_data=context_data,
    )
    _render_task_overview(overview)
    _render_flow_steps(run_dir)

    # ── P6: Source Intake (source list + server-local developer entry) ──
    with st.expander("📎 当前资料 / Sources", expanded=False):
        st.caption("普通资料上传请直接使用底部聊天框附件。这里保留资料列表和服务器本地路径入口。")

        local_path = st.text_input(
            "服务器本地文件路径",
            placeholder="/root/autodl-tmp/AI4S/2303.15140v2.pdf",
            key="_source_local_path",
        )
        if st.button("添加本地文件", type="secondary", disabled=not local_path.strip()):
            try:
                info = register_local_file_source(run_dir, local_path.strip())
            except Exception as exc:
                st.error(f"添加失败：{exc}")
            else:
                st.success(f"✅ 已添加：{info['stored_path']}")
                st.caption(f"现在可以在聊天框中说：读一下 {info['stored_path']}")
                st.rerun()

        st.caption("— 添加链接 / 仓库 / 文字 —")
        url_input = st.text_input(
            "网页或 GitHub 地址",
            placeholder="https://github.com/amazon-science/patchcore-inspection",
            key="_source_url_input",
        )
        if st.button("添加链接", type="secondary", disabled=not url_input.strip()):
            try:
                from autoad_researcher.ui.sources import register_url_source
                info = register_url_source(run_dir, url_input.strip())
            except Exception as exc:
                st.error(f"添加失败：{exc}")
            else:
                st.success(f"✅ 已登记：{info['source_id']}（{info['kind']}）")
                st.caption("source 已记录，intake 待触发。")
                st.rerun()

        user_text = st.text_area(
            "手写研究说明",
            placeholder="目标数据集为 MVTec AD，关注纹理类异常检测...",
            key="_source_user_text_input",
        )
        if st.button("保存文字", type="secondary", disabled=not user_text.strip()):
            try:
                from autoad_researcher.ui.sources import register_user_text_source
                info = register_user_text_source(run_dir, user_text.strip())
            except Exception as exc:
                st.error(f"保存失败：{exc}")
            else:
                st.success(f"✅ 已保存：{info['source_id']}")
                st.rerun()

        src_ctx = get_source_context(run_dir)
        if src_ctx:
            st.code(src_ctx, language="text")
        else:
            st.caption("暂无已添加的资料。支持 PDF / txt / md 上传。")

    _render_source_cards_panel(run_dir)
    _render_evidence_boundary_panel(run_dir)
    _render_freeze_panel(run_dir)

    if not api_key:
        st.warning("请先在「运行配置」中填写 API Key。")
        _render_developer_info(
            run_dir=run_dir,
            overview=overview,
            provider_url=provider_url,
            dataset_root=dataset_root,
            context_data=context_data,
        )
        return

    st.markdown("---")
    st.subheader("研究助手")
    st.caption("请描述你想做的实验、复现目标或改进方向。")
    mode = st.segmented_control(
        "助手模式",
        options=list(MODE_PROMPTS.keys()),
        format_func=lambda m: _MODE_LABELS[m],
        key="_chat_mode",
        default="intent_clarification",
    )

    transcript = load_transcript(run_dir)
    _render_transcript(transcript)
    _handle_chat_input(
        run_dir=run_dir,
        mode=mode,
        api_key=api_key,
        provider_url=provider_url,
        context_data=context_data,
    )

    st.markdown("---")
    _render_intent_draft_panel(
        run_dir=run_dir,
        mode=mode,
        api_key=api_key,
        provider_url=provider_url,
        context_data=context_data,
    )

    st.markdown("---")
    _render_pipeline_input_panel(run_dir)

    st.markdown("---")
    _render_stage_approval_panel(run_dir)

    _render_developer_info(
        run_dir=run_dir,
        overview=overview,
        provider_url=provider_url,
        dataset_root=dataset_root,
        context_data=context_data,
    )


def _resolve_run_dir(browse_id: str) -> Path | None:
    try:
        return run_dir_path("runs", browse_id)
    except ValueError:
        return None


def _render_task_overview(overview: dict[str, Any]) -> None:
    st.subheader("当前任务")
    st.markdown(f"**{overview['task_title']}**")
    if overview.get("task_summary"):
        st.caption(str(overview["task_summary"]))
    cols = st.columns(2)
    cols[0].metric("状态", str(overview["status_label"]))
    cols[1].metric("数据集", str(overview["dataset_status"]))


def _render_transcript(transcript: list[dict]) -> None:
    for entry in transcript:
        role = entry.get("role", "user")
        content = entry.get("content", "")
        if role == "user":
            st.chat_message("user").write(content)
        else:
            st.chat_message("assistant").write(content)


def build_research_chat_messages(
    *,
    run_dir: Path,
    mode: str,
    user_input: str,
    context_data: dict | None,
    transcript_tail: list[dict] | None = None,
) -> list[dict[str, str]]:
    """Assemble messages for a research chat LLM call.

    For intent_clarification mode, injects WhatWeKnow from silent_probe
    and SourceReferences from the source registry as separate system messages.
    *transcript_tail* provides recent chat history so the LLM remembers context.
    """
    from autoad_researcher.ui.chat_prompts import MODE_PROMPTS

    system_prompt = MODE_PROMPTS[mode]
    context_str = json.dumps(context_data, ensure_ascii=False, default=str) if context_data else "{}"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]

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


def _execute_or_report_pdf_parse_action(run_dir: Path, action: dict[str, Any]) -> str:
    kind = str(action.get("action", ""))
    if kind not in _RESEARCH_CHAT_ALLOWED_ACTIONS:
        return _reject_research_chat_action(run_dir, kind, action)
    decision: ActionDecision | None = action.get("action_decision")
    if kind == "parse":
        pdf_path = Path(action["pdf_path"])
        source_id = action.get("source_id")
        if decision is not None:
            append_action_decision(run_dir, decision.model_copy(update={"execution_status": "planned"}))
        if source_id:
            update_source_status(run_dir, str(source_id), "parsing")
        with st.spinner(f"正在解析 {pdf_path.name}，论文解析可能需要 5-10 分钟…"):
            result = _run_paper_intelligence(run_dir.name, pdf_path)
        if result["status"] == "parsed":
            refreshed = build_research_context_snapshot(run_dir)
            if refreshed.paper_artifact_quality != "usable":
                err = "paper artifacts 质量不足，不能基于论文正文回答"
                if source_id:
                    update_source_status(run_dir, str(source_id), "failed", error_message=err)
                if decision is not None:
                    append_action_decision(
                        run_dir,
                        decision.model_copy(update={
                            "execution_status": "executed_failed",
                            "source_status_after": "parsing_failed",
                            "error_code": "PAPER_ARTIFACTS_INSUFFICIENT",
                            "user_visible_error": err,
                        }),
                    )
                reply = f"⚠️ {pdf_path.name} 解析流程已完成，但生成的 paper artifacts 证据不足。当前不能基于论文正文作可靠判断。"
                st.warning(reply)
                return reply
            if source_id:
                update_source_status(run_dir, str(source_id), "parsed")
            if decision is not None:
                append_action_decision(
                    run_dir,
                    decision.model_copy(update={
                        "execution_status": "executed_success",
                        "source_status_after": "parsed",
                    }),
                )
            reply_parts = [f"✅ {pdf_path.name} 已完成 paper-intelligence 解析。"]
            if refreshed.paper_methods:
                methods = "；".join(refreshed.paper_methods[:5])
                reply_parts.append(f"我从 artifacts 看到：{methods}")
            if refreshed.missing_blocking_gaps:
                gaps = "、".join(refreshed.missing_blocking_gaps[:5])
                reply_parts.append(f"仍缺：{gaps}")
            reply_parts.append("后续回答将只基于可用 paper artifacts。")
            reply = "\n\n".join(reply_parts)
            st.success(reply)
            return reply
        err = result.get("error", "未知错误")
        if source_id:
            update_source_status(run_dir, str(source_id), "failed", error_message=err)
        if decision is not None:
            append_action_decision(
                run_dir,
                decision.model_copy(update={
                    "execution_status": "executed_failed",
                    "source_status_after": "parsing_failed",
                    "error_code": "PAPER_PARSE_FAILED",
                    "user_visible_error": "PDF 解析未成功，请检查文件是否完整",
                }),
            )
        reply = f"❌ {pdf_path.name} 解析失败：{err}"
        st.error(reply)
        return reply

    message = str(action["message"])
    if decision is not None:
        append_action_decision(run_dir, decision)
    if kind in {"missing", "choose", "parse_failed", "blocked"}:
        st.warning(message)
    else:
        st.info(message)
    return message


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
    if st is not None:
        st.warning(message)
    return message


def _handle_chat_input(
    *,
    run_dir: Path,
    mode: str,
    api_key: str,
    provider_url: str,
    context_data: dict | None,
) -> None:
    submission = st.chat_input(
        "输入你的问题，或附加论文 PDF / 材料…",
        key="_chat_input",
        accept_file="multiple",
        file_type=["pdf", "txt", "md", "markdown"],
        max_upload_size=200,
    )
    if not submission:
        return
    user_input, attached_files = normalize_chat_submission(submission)
    if not user_input and not attached_files:
        return

    # ── P3: Read transcript history BEFORE saving current input ──
    existing_transcript = load_transcript(run_dir)
    transcript_tail = existing_transcript[-10:]

    attached_sources = save_chat_attachments(run_dir, attached_files)
    user_content = user_input or _attachment_user_message(attached_sources)
    save_transcript(run_dir, mode, "user", user_content)
    st.chat_message("user").write(user_content)

    if attached_sources and not user_input:
        pdf_sources = [s for s in attached_sources if s.get("kind") == "paper_pdf"]
        if len(pdf_sources) == 1:
            parse_action = build_pdf_parse_action(run_dir, user_content, recent_sources=attached_sources)
            if parse_action["action"] != "chat":
                reply = _execute_or_report_pdf_parse_action(run_dir, parse_action)
                st.chat_message("assistant").write(reply)
                save_transcript(run_dir, mode, "assistant", reply)
                return
        reply = build_attachment_added_reply(attached_sources)
        st.chat_message("assistant").write(reply)
        save_transcript(run_dir, mode, "assistant", reply)
        return

    # ── P6: Parse trigger — natural language PDF parse requests ──
    parse_action = build_pdf_parse_action(run_dir, user_input, recent_sources=attached_sources)
    if parse_action["action"] != "chat":
        reply = _execute_or_report_pdf_parse_action(run_dir, parse_action)
        st.chat_message("assistant").write(reply)
        save_transcript(run_dir, mode, "assistant", reply)
        return

    if attached_sources:
        reply = build_attachment_added_reply(attached_sources)
        st.chat_message("assistant").write(reply)
        save_transcript(run_dir, mode, "assistant", reply)
        return

    if not st.session_state.get("_first_task_message_handled"):
        st.session_state._first_task_message_handled = True
        existing, _warning = safe_load_task_profile(run_dir)
        if existing is None:
            try:
                profile = generate_task_profile_from_first_message(
                    run_dir=run_dir,
                    api_key=api_key,
                    provider_base_url=provider_url,
                    first_user_message=user_input,
                )
                save_task_profile(run_dir, profile)
            except Exception:
                pass  # Never block chat on title generation.

    messages = build_research_chat_messages(
        run_dir=run_dir,
        mode=mode,
        user_input=user_input,
        context_data=context_data,
        transcript_tail=transcript_tail,
    )

    with st.spinner("思考中…"):
        result = call_research_chat(
            api_key=api_key,
            provider_base_url=provider_url,
            messages=messages,
        )

    if result["error"]:
        st.error(result["error"])
        return

    evidence_context = build_research_chat_evidence_context(run_dir)
    guarded = guard_research_chat_reply(
        reply=result["reply"],
        user_input=user_input,
        evidence_context=evidence_context,
    )
    reply = guarded.reply
    st.chat_message("assistant").write(reply)
    save_transcript(
        run_dir,
        mode,
        "assistant",
        reply,
        context_refs=list(context_data.keys()) if context_data else [],
    )
    st.rerun()


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


def _render_source_cards_panel(run_dir: Path) -> None:
    rows = build_source_card_rows(run_dir)
    with st.expander("资料状态与解析版本", expanded=False):
        if not rows:
            st.caption("暂无资料。")
            return
        st.table([
            {
                "source_id": row["source_id"],
                "类型": row["kind"],
                "状态": row["status"],
                "active attempt": row["active_parse_attempt_id"] or "—",
                "attempts": row["parse_attempt_count"],
            }
            for row in rows
        ])
        for row in rows:
            _render_attempt_timeline(run_dir, row)

def _render_attempt_timeline(run_dir: Path, row: dict[str, Any]) -> None:
    """Render parse attempt timeline for a single source with switch controls."""
    attempts = row.get("attempts", [])
    if not attempts:
        return

    active_id = row.get("active_parse_attempt_id")
    has_ok_attempt = any(
        a.get("status") == "ok" for a in attempts
    )

    with st.container():
        st.markdown(f"---")
        st.caption(f"📋 {row.get('label', row.get('source_id', ''))} · 解析记录")
        for idx, attempt in enumerate(attempts):
            pa_id = attempt.get("parse_attempt_id", "—")
            status = attempt.get("status", "—")
            parser = attempt.get("parser", "—")
            is_active = bool(attempt.get("active"))

            status_icon = {
                "ok": "✅",
                "partial": "⚠️",
                "failed": "❌",
                "running": "🔄",
            }.get(status, "⬜")

            cols = st.columns([0.6, 1.4, 1.0, 1.4])
            with cols[0]:
                st.caption(f"{status_icon} {pa_id}")
            with cols[1]:
                st.caption(f"parser: {parser} | 状态: {status}")
            with cols[2]:
                if is_active:
                    st.success("当前使用", icon="")
                elif status == "ok":
                    st.info("可切换")
                elif status == "failed":
                    st.caption("失败")

            with cols[3]:
                if not is_active and attempt.get("parse_attempt_id"):
                    disabled = status == "failed" and has_ok_attempt
                    if st.button(
                        "设为当前",
                        key=f"_active_attempt_{row['source_id']}_{pa_id}_{idx}",
                        disabled=disabled,
                    ):
                        set_active_parse_attempt(
                            run_dir,
                            str(row["source_id"]),
                            str(pa_id),
                            reason="ui_source_card_switch",
                        )
                        st.success("已切换")
                        st.rerun()

            if status == "failed":
                reason = attempt.get("reason") or row.get("error_message") or ""
                if reason:
                    st.caption(f"失败原因: {reason}")

            quality = attempt.get("quality_report")
            if quality:
                st.caption(f"质量报告: {quality}")


def _render_evidence_boundary_panel(run_dir: Path) -> None:
    """Show what the system can and cannot answer based on available evidence."""
    rows = build_source_card_rows(run_dir)
    if not rows:
        return

    parsed = [r for r in rows if r["status"] == "parsed"]
    failed = [r for r in rows if r["status"] == "failed"]
    unparsed = [r for r in rows if r["status"] in {"uploaded_not_parsed", "user_provided_not_ingested", "parsing"}]
    user_text = [r for r in rows if r["kind"] == "user_text"]

    with st.expander("证据边界", expanded=False):
        if parsed:
            st.success(f"可引用（{len(parsed)} 个来源）")
            for src in parsed:
                st.caption(f"  {src['label'] or src['source_id']} · {src['active_parse_attempt_id'] or 'legacy'}")
        else:
            st.caption("暂无已解析来源。")

        if user_text:
            st.info(f"用户提供（{len(user_text)} 个来源）")

        if unparsed:
            st.warning(f"未解析（{len(unparsed)} 个来源）")
            for src in unparsed:
                st.caption(f"  {src['label'] or src['source_id']}")

        if failed:
            st.error(f"解析失败（{len(failed)} 个来源）")
            for src in failed:
                err = src.get("error_message") or ""
                st.caption(f"  {src['label'] or src['source_id']}{' · ' + err if err else ''}")

        freeze_state = build_freeze_panel_state(run_dir)
        if freeze_state.get("active_freeze_version"):
            st.caption(f"当前冻结版本: {freeze_state['active_freeze_version']}（后续实验 agents 将读取此版本）")


def _render_freeze_panel(run_dir: Path) -> None:
    state = build_freeze_panel_state(run_dir)
    with st.expander("冻结研究上下文", expanded=False):
        if state["active_freeze_version"]:
            st.info(f"当前冻结版本：{state['active_freeze_version']}")
        elif state["draft_exists"]:
            st.caption("已有 ResearchContextDraft，可以冻结当前资料与证据边界。")
        else:
            st.caption("尚无 ResearchContextDraft，暂不能冻结。")
        if st.button("冻结当前研究上下文", type="primary", disabled=not state["button_enabled"]):
            try:
                result = freeze_context(run_dir)
            except Exception as exc:
                st.error(f"冻结失败：{exc}")
            else:
                st.success(f"已生成 {result['freeze_version']}")
                st.rerun()
        if state["freezes"]:
            st.table(state["freezes"])


def _render_intent_draft_panel(
    *,
    run_dir: Path,
    mode: str,
    api_key: str,
    provider_url: str,
    context_data: dict | None,
) -> None:
    st.subheader("研究目标草案")
    existing = load_intent_draft(run_dir)
    transcript = load_transcript(run_dir)
    intent_messages = [entry for entry in transcript if entry.get("mode") == "intent_clarification"]

    if mode != "intent_clarification":
        st.info("切换到「意图澄清」后，可以把聊天内容整理成研究目标草案。")
    elif not intent_messages:
        st.info("先描述你的复现目标、实验想法或改进方向。")
    else:
        if st.button("生成研究目标草案", type="secondary"):
            messages = intent_draft_prompt_payload(
                run_id=run_dir.name,
                transcript_tail=intent_messages,
                context=context_data,
            )
            with st.spinner("正在整理研究目标草案…"):
                result = call_research_chat(
                    api_key=api_key,
                    provider_base_url=provider_url,
                    messages=messages,
                )
            if result["error"]:
                st.error(result["error"])
            else:
                try:
                    draft = parse_intent_draft_response(result["reply"], run_id=run_dir.name)
                except ValueError as exc:
                    st.error(f"草案无法解析：{exc}")
                else:
                    save_intent_draft(run_dir, draft)
                    save_clarification_input(run_dir, draft)
                    st.success("研究目标草案已生成，请检查后确认。")
                    st.rerun()

    draft = load_intent_draft(run_dir) or existing
    if draft:
        st.markdown(render_intent_draft_markdown(draft))
        _render_confirmation_panel(run_dir)


def _render_confirmation_panel(run_dir: Path) -> None:
    draft = load_intent_draft(run_dir)
    confirmation = load_intent_confirmation(run_dir)
    if confirmation:
        label = {
            "approved": "已确认",
            "needs_revision": "需要修改",
            "rejected": "已放弃",
        }[confirmation.decision]
        st.info(f"研究目标状态：{label}")
        if confirmation.comment:
            st.caption(f"备注：{confirmation.comment}")

    if not draft:
        return

    comment = st.text_area("确认备注（可选）", key="_intent_confirmation_comment")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("确认这个研究目标", type="primary"):
            save_intent_confirmation(run_dir, decision="approved", comment=comment or None)
            st.success("研究目标已确认。")
            st.rerun()
    with col2:
        if st.button("需要修改"):
            save_intent_confirmation(run_dir, decision="needs_revision", comment=comment or None)
            st.warning("已标记为需要修改。")
            st.rerun()
    with col3:
        if st.button("放弃"):
            save_intent_confirmation(run_dir, decision="rejected", comment=comment or None)
            st.error("已放弃当前研究目标。")
            st.rerun()


def _render_pipeline_input_panel(run_dir: Path) -> None:
    action = build_pipeline_input_action(run_dir)
    st.subheader("下一步")
    st.write(action["message"])

    if action["button_enabled"]:
        if st.button("生成实验输入", type="primary"):
            try:
                save_input_task_yaml_from_clarification(run_dir)
            except Exception as exc:
                st.error(f"生成失败：{exc}")
            else:
                st.success("实验输入已准备好。")
                st.rerun()


def _render_flow_steps(run_dir: Path) -> None:
    st.subheader("当前流程")
    lines = []
    for step in build_user_flow_steps(run_dir):
        marker = "← 当前步骤" if step["state"] == "current" else "✓" if step["state"] == "done" else ""
        suffix = f" {marker}" if marker else ""
        lines.append(f"{step['index']}. {step['label']}{suffix}")
    st.markdown("\n".join(lines))


def _render_stage_approval_panel(run_dir: Path) -> None:
    request_path = run_dir / "patch_planner" / "patch_planner_approval_request.json"
    handoff_path = run_dir / "patch_applicator" / "patch_runner_handoff.json"
    has_any_approval = request_path.is_file() or handoff_path.is_file()

    if not has_any_approval:
        st.info("后续 pipeline 到达相应阶段后，会在这里请求你的审批。")
        return

    st.subheader("需要你审批")
    if request_path.is_file():
        _render_patch_approval_panel(run_dir)
    if handoff_path.is_file():
        if request_path.is_file():
            st.markdown("---")
        _render_run_approval_panel(run_dir)


def _render_patch_approval_panel(run_dir: Path) -> None:
    st.markdown("**审批代码修改方案**")
    diff_path = run_dir / "patch_planner" / "proposed_patch.diff"
    validation_path = run_dir / "patch_planner" / "patch_payload_validation_report.json"
    approval = load_stage3_approval(run_dir, decision_type="patch_approval")

    if approval:
        st.info("代码修改方案已确认。" if approval.confirmed_by_user else "代码修改方案已被拒绝。")

    st.caption("请审阅修改方案、diff 和风险后再确认。")
    if validation_path.is_file():
        with st.expander("查看修改校验报告"):
            try:
                st.json(json.loads(validation_path.read_text(encoding="utf-8")))
            except Exception:
                st.code(validation_path.read_text(encoding="utf-8")[:4000])
    if diff_path.is_file():
        with st.expander("查看代码差异"):
            st.code(diff_path.read_text(encoding="utf-8")[:8000], language="diff")

    comment = st.text_area("代码修改审批备注（可选）", key="_patch_approval_comment")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("确认代码修改方案", type="primary"):
            save_stage3_approval(
                run_dir,
                decision_type="patch_approval",
                confirmed_by_user=True,
                user_confirmation_text=comment or "I approve the proposed patch plan.",
            )
            st.success("代码修改方案已确认。")
            st.rerun()
    with col2:
        if st.button("拒绝代码修改方案"):
            save_stage3_approval(
                run_dir,
                decision_type="patch_approval",
                confirmed_by_user=False,
                user_confirmation_text=comment or "I reject the proposed patch plan.",
            )
            st.warning("已拒绝代码修改方案。")
            st.rerun()


def _render_run_approval_panel(run_dir: Path) -> None:
    st.markdown("**审批真实执行**")
    intake_path = run_dir / "runner_execute" / "runner_intake_report.json"
    approval = load_stage3_approval(run_dir, decision_type="run_approval")

    if approval:
        st.info("真实执行已确认。" if approval.confirmed_by_user else "真实执行已被拒绝。")

    st.warning(
        "真实执行会运行 GPU benchmark、读取数据集并产生实验结果。"
        "确认后仍需在终端设置 AUTOAD_L3_REAL_EXECUTION_ALLOWED=1。"
    )
    if intake_path.is_file():
        with st.expander("查看执行准入报告"):
            try:
                st.json(json.loads(intake_path.read_text(encoding="utf-8")))
            except Exception:
                st.code(intake_path.read_text(encoding="utf-8")[:4000])

    comment = st.text_area("真实执行审批备注（可选）", key="_run_approval_comment")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("确认真实执行", type="primary"):
            save_stage3_approval(
                run_dir,
                decision_type="run_approval",
                confirmed_by_user=True,
                user_confirmation_text=comment or "I approve real L3 execution.",
            )
            st.success("真实执行已确认。")
            st.rerun()
    with col2:
        if st.button("拒绝真实执行"):
            save_stage3_approval(
                run_dir,
                decision_type="run_approval",
                confirmed_by_user=False,
                user_confirmation_text=comment or "I reject real L3 execution.",
            )
            st.warning("已拒绝真实执行。")
            st.rerun()


# ---------------------------------------------------------------------------
# Developer info and pure helpers
# ---------------------------------------------------------------------------


def _render_developer_info(
    *,
    run_dir: Path,
    overview: dict[str, Any],
    provider_url: str,
    dataset_root: str,
    context_data: dict | None,
) -> None:
    with st.expander("开发者信息", expanded=False):
        st.json(build_developer_info_payload(
            run_dir,
            overview=overview,
            provider_url=provider_url,
            dataset_root=dataset_root,
            context_data=context_data,
        ))
        st.markdown("**Approval gate status**")
        st.table(build_hitl_gate_status_rows(run_dir))
        draft = load_intent_draft(run_dir)
        if draft:
            with st.expander("intent_draft.json"):
                st.json(draft.model_dump(mode="json"))
        with st.expander("发送给 LLM 的上下文"):
            if context_data:
                st.json(context_data)
            else:
                st.caption("无上下文数据。")


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
