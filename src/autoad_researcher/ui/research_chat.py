"""Research Assistant Chat — advisory UI with Phase 2B intent checkpoints."""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import streamlit as st
except ModuleNotFoundError:  # UI extra is optional in CI/unit-test environments.
    st = None

from autoad_researcher.ui.artifact_viewer import run_dir_path
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
)

_SAFETY_WARNING = "研究助手只提供解释和建议，不会修改代码，也不会执行真实 L3。"
_MODE_LABELS = {
    "intent_clarification": "意图澄清 — 描述研究想法，让系统整理成实验目标",
    "run_explanation": "运行解释 — 看不懂当前结果，让系统解释",
    "next_experiment": "下一步建议 — 跑完了，让系统建议下一轮实验",
}


def render_research_chat():
    if st is None:
        raise RuntimeError("streamlit is required to render the Research Assistant UI")
    st.title("研究助手")
    st.warning(_SAFETY_WARNING, icon="🛡️")

    api_key = st.session_state.get("_api_key_raw")
    provider_url = st.session_state.get("provider_base_url", "https://api.deepseek.com")
    browse_id = st.session_state.get("_browse_run_id", st.session_state.get("_run_id_hash", ""))

    run_dir = _resolve_run_dir(browse_id)
    context_data = build_chat_context(run_dir) if run_dir and run_dir.is_dir() else None

    _render_context_banner(browse_id=browse_id, context_data=context_data)

    if run_dir is None:
        st.info("请先在侧边栏输入合法 Run ID，或在「运行配置」中生成新的运行 ID。")
        return

    if not api_key:
        st.warning("请先在「运行配置」中填写 API Key。")
        return

    st.markdown("---")
    st.subheader("LLM 研究助手")
    mode = st.selectbox(
        "你现在想做什么？",
        options=list(MODE_PROMPTS.keys()),
        format_func=lambda m: _MODE_LABELS[m],
        key="_chat_mode",
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
    _render_confirmation_panel(run_dir)

    with st.expander("查看发送给 LLM 的上下文"):
        if context_data:
            st.json(context_data)
        else:
            st.caption("无上下文数据。")


def _resolve_run_dir(browse_id: str) -> Path | None:
    try:
        return run_dir_path("runs", browse_id)
    except ValueError:
        return None


def _render_context_banner(*, browse_id: str, context_data: dict | None) -> None:
    st.subheader("当前运行上下文")
    if context_data:
        available = context_data.get("available_stages", [])
        st.caption(
            f"当前运行: `{browse_id}`  |  "
            f"数据集: `{st.session_state.get('dataset_root', '—')}`  |  "
            f"Provider: DeepSeek  |  "
            f"可用阶段: {', '.join(available) if available else '无'}"
        )
    else:
        st.caption(f"浏览: `{browse_id}` — 尚无制品数据")


def _render_transcript(transcript: list[dict]) -> None:
    for entry in transcript:
        role = entry.get("role", "user")
        content = entry.get("content", "")
        entry_mode = entry.get("mode", "")
        if role == "user":
            st.chat_message("user").write(f"[{entry_mode}] {content}")
        else:
            st.chat_message("assistant").write(content)


def _handle_chat_input(
    *,
    run_dir: Path,
    mode: str,
    api_key: str,
    provider_url: str,
    context_data: dict | None,
) -> None:
    user_input = st.chat_input("输入你的问题…", key="_chat_input")
    if not user_input:
        return

    save_transcript(run_dir, mode, "user", user_input)
    st.chat_message("user").write(f"[{mode}] {user_input}")

    system_prompt = MODE_PROMPTS[mode]
    context_str = json.dumps(context_data, ensure_ascii=False, default=str) if context_data else "{}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": "当前运行上下文:\n" + context_str},
        {"role": "user", "content": user_input},
    ]

    with st.spinner("思考中…"):
        result = call_research_chat(
            api_key=api_key,
            provider_base_url=provider_url,
            messages=messages,
        )

    if result["error"]:
        st.error(result["error"])
        return

    reply = result["reply"]
    st.chat_message("assistant").write(reply)
    save_transcript(
        run_dir,
        mode,
        "assistant",
        reply,
        context_refs=list(context_data.keys()) if context_data else [],
    )
    st.rerun()


def _render_intent_draft_panel(
    *,
    run_dir: Path,
    mode: str,
    api_key: str,
    provider_url: str,
    context_data: dict | None,
) -> None:
    st.subheader("研究意图草案")
    st.caption("草案保存在 `runs/{run_id}/ui_chat/`，属于 UI 审计材料；不会触发 pipeline。")

    existing = load_intent_draft(run_dir)
    transcript = load_transcript(run_dir)
    intent_messages = [entry for entry in transcript if entry.get("mode") == "intent_clarification"]

    if mode != "intent_clarification":
        st.info("切换到「意图澄清」模式后，可以从聊天内容生成研究意图草案。")
    elif not intent_messages:
        st.info("先在「意图澄清」模式中描述研究想法，再生成草案。")
    else:
        if st.button(
            "生成研究意图草案",
            type="secondary",
            help="调用 LLM 输出严格 JSON，保存为 ui_chat/intent_draft.json；不会执行 pipeline。",
        ):
            messages = intent_draft_prompt_payload(
                run_id=run_dir.name,
                transcript_tail=intent_messages,
                context=context_data,
            )
            with st.spinner("正在生成结构化草案…"):
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
                    st.error(f"草案 JSON 无法解析：{exc}")
                else:
                    save_intent_draft(run_dir, draft)
                    save_clarification_input(run_dir, draft)
                    save_transcript(
                        run_dir,
                        "intent_clarification",
                        "assistant",
                        "已生成研究意图草案：ui_chat/intent_draft.json",
                        context_refs=["ui_chat/intent_draft.json", "ui_chat/clarification_input.json"],
                    )
                    st.success("已保存 intent_draft.json 和 clarification_input.json。")
                    st.rerun()

    draft = load_intent_draft(run_dir) or existing
    if draft:
        st.markdown("**当前草案**")
        st.write(draft.research_goal)
        cols = st.columns(2)
        with cols[0]:
            st.markdown("**主要指标**")
            st.write(draft.primary_metrics or ["none"])
            st.markdown("**允许修改范围**")
            st.write(draft.allowed_change_scope or ["none"])
        with cols[1]:
            st.markdown("**底线指标**")
            st.write(draft.guardrail_metrics or ["none"])
            st.markdown("**禁止修改范围**")
            st.write(draft.forbidden_change_scope or ["none"])
        st.markdown("**成功标准**")
        st.write(draft.success_criteria)
        with st.expander("查看 intent_draft.json"):
            st.json(draft.model_dump(mode="json"))


def _render_confirmation_panel(run_dir: Path) -> None:
    st.subheader("人工确认状态")
    st.caption("确认研究意图只表示用户认可该草案；不会自动执行 patch-plan、patch-apply 或真实 L3。")

    draft = load_intent_draft(run_dir)
    confirmation = load_intent_confirmation(run_dir)
    if confirmation:
        st.info(
            f"当前确认状态: `{confirmation.decision}`  |  reviewer: `{confirmation.reviewer}`  |  "
            f"created_at: `{confirmation.created_at}`"
        )
        if confirmation.comment:
            st.caption(f"备注: {confirmation.comment}")

    if not draft:
        st.warning("尚无 intent_draft.json，无法确认。")
        return

    comment = st.text_area("确认备注（可选）", key="_intent_confirmation_comment")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("确认采用", type="primary"):
            save_intent_confirmation(run_dir, decision="approved", comment=comment or None)
            st.success("已写入 approvals/intent_confirmation.json。")
            st.rerun()
    with col2:
        if st.button("需要修改"):
            save_intent_confirmation(run_dir, decision="needs_revision", comment=comment or None)
            st.warning("已记录 needs_revision。")
            st.rerun()
    with col3:
        if st.button("驳回"):
            save_intent_confirmation(run_dir, decision="rejected", comment=comment or None)
            st.error("已记录 rejected。")
            st.rerun()


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
