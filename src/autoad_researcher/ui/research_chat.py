"""Research Assistant Chat — Phase 2A advisory-only page."""

import json
import re
from pathlib import Path

import streamlit as st

from autoad_researcher.ui.artifact_viewer import run_dir_path
from autoad_researcher.ui.chat_client import call_research_chat
from autoad_researcher.ui.chat_context import build_chat_context
from autoad_researcher.ui.chat_prompts import MODE_PROMPTS
from autoad_researcher.ui.chat_transcript import load_transcript, save_transcript

_SAFETY_WARNING = "研究助手只提供解释和建议，不会修改代码，也不会执行真实 L3。"
_MODE_LABELS = {
    "intent_clarification": "意图澄清 — 描述研究想法，让系统整理成实验目标",
    "run_explanation": "运行解释 — 看不懂当前结果，让系统解释",
    "next_experiment": "下一步建议 — 跑完了，让系统建议下一轮实验",
}


def render_research_chat():
    st.title("研究助手")
    st.warning(_SAFETY_WARNING, icon="🛡️")

    api_key = st.session_state.get("_api_key_raw")
    provider_url = st.session_state.get("provider_base_url", "https://api.deepseek.com")
    browse_id = st.session_state.get("_browse_run_id", st.session_state.get("_run_id_hash", ""))

    # ── Context banner ───────────────────────────────────────────────────
    try:
        run_dir = run_dir_path("runs", browse_id)
    except ValueError:
        run_dir = None

    context_data = build_chat_context(run_dir) if run_dir and run_dir.is_dir() else None

    if context_data:
        available = context_data.get("available_stages", [])
        st.caption(
            f"当前运行: `{browse_id}`  |  "
            f"数据集: `{st.session_state.get('dataset_root', '—')}`  |  "
            f"可用阶段: {', '.join(available) if available else '无'}"
        )
    else:
        st.caption(f"浏览: `{browse_id}` — 尚无制品数据")

    if not api_key:
        st.warning("请先在「运行配置」中填写 API Key。")
        return

    # ── Mode selector ────────────────────────────────────────────────────
    mode = st.selectbox(
        "你现在想做什么？",
        options=list(MODE_PROMPTS.keys()),
        format_func=lambda m: _MODE_LABELS[m],
        key="_chat_mode",
    )

    # ── Chat history ─────────────────────────────────────────────────────
    transcript = load_transcript(run_dir) if run_dir else []
    for entry in transcript:
        role = entry.get("role", "user")
        content = entry.get("content", "")
        entry_mode = entry.get("mode", "")
        if role == "user":
            st.chat_message("user").write(f"[{entry_mode}] {content}")
        else:
            st.chat_message("assistant").write(content)

    # ── Chat input ───────────────────────────────────────────────────────
    if run_dir is None:
        st.info("请先在侧边栏输入合法 Run ID，或在「运行配置」中生成新的运行 ID。")
        st.stop()

    user_input = st.chat_input("输入你的问题…", key="_chat_input")
    if user_input:
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
        else:
            reply = result["reply"]
            st.chat_message("assistant").write(reply)
            save_transcript(run_dir, mode, "assistant", reply,
                            context_refs=list(context_data.keys()) if context_data else [])
            st.rerun()

    # ── Intent draft button (intent_clarification only) ──────────────────
    if mode == "intent_clarification":
        transcript = load_transcript(run_dir)
        last_assistant = ""
        for e in reversed(transcript):
            if e.get("role") == "assistant" and e.get("mode") == mode:
                last_assistant = e.get("content", "")
                break

        if last_assistant:
            col1, col2 = st.columns([1, 3])
            with col1:
                if st.button("📋 生成实验意图草案 JSON", type="secondary",
                             help="提取 LLM 回复中的结构化字段，生成草稿 JSON（仅展示，不写入 pipeline）"):
                    draft = _extract_intent_draft(last_assistant)
                    st.session_state._intent_draft = draft

            if st.session_state.get("_intent_draft"):
                draft = st.session_state._intent_draft
                with col2:
                    st.caption("仅展示，未写入任何 pipeline artifact。Phase 2B 可转为 clarified_task.json。")
                st.code(json.dumps(draft, ensure_ascii=False, indent=2), language="json")

    # ── Raw context viewer ───────────────────────────────────────────────
    with st.expander("查看发送给 LLM 的上下文"):
        if context_data:
            st.json(context_data)
        else:
            st.caption("无上下文数据。")


# ── Intent draft extraction ─────────────────────────────────────────────

def _extract_intent_draft(text: str) -> dict:
    """Best-effort extraction of structured intent from LLM reply.

    Returns a dict loosely aligned with ClarifiedTask fields.
    This is a display-only draft — it never writes to pipeline artifacts.
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
