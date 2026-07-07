"""V2 主对话页 — Claude Code / OpenCode 风格。

- 工具调用内联显示（● 解析中… → ✓ 解析完成 (2.1s)）
- 不阻塞主界面（st.status 替代 st.spinner）
- 底部键盘提示
- 演示按钮完整模拟
"""

from __future__ import annotations

import time
from datetime import datetime

import streamlit as st

from autoad_researcher.ui.v2_status import render_status_bar
from autoad_researcher.ui.v2_toast import show_toast

CHAT_CONTAINER_HEIGHT = 500

# ── Styles ──
STATUS_CSS = """
<style>
.tool-line { margin: 3px 0; font-size: 0.92em; }
.tool-pending { color: #888; }
.tool-done { color: #66bb6a; }
.tool-error { color: #ef5350; }
.tool-info { color: #4fc3f7; }
.kbd-hint { color: #555; font-size: 0.75em; margin-top: 2px; }
</style>
"""


def _init_session() -> None:
    defaults = {
        "v2_messages": [],  # list of dicts: {role, content, tool_lines: [...]}
        "v2_run_id": f"run_{datetime.now().strftime('%Y%m%d_%H%M_%f')}",
        "v2_pending_jobs": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def render_chat_page() -> None:
    _init_session()
    st.markdown(STATUS_CSS, unsafe_allow_html=True)

    # ── 消息区（固定高度，可滚动）──
    chat_container = st.container(height=CHAT_CONTAINER_HEIGHT, border=False)
    with chat_container:
        _render_messages()

    # ── 底部输入区 ──
    st.markdown("---")
    col_input, col_plus = st.columns([28, 1], vertical_alignment="bottom")
    with col_input:
        user_input = st.chat_input(
            "输入问题，或粘贴 URL…",
            key="v2_chat_input",
        )
    with col_plus:
        _render_plus_popover()

    if user_input:
        _handle_user_input(user_input)

    # ── 键盘提示 ──
    st.markdown(
        '<p class="kbd-hint">Enter 发送 · Shift+Enter 换行 · 右上角 🔔 看演示</p>',
        unsafe_allow_html=True,
    )

    # ── 状态栏 ──
    render_status_bar(
        total_sources=st.session_state.v2_pending_jobs,
        parsed_sources=0,
        pending_jobs=st.session_state.v2_pending_jobs,
        usable_evidence=0,
        candidate_only=0,
    )


def _render_messages() -> None:
    msgs = st.session_state.v2_messages

    if not msgs:
        st.markdown("#### 👋 AutoAD Researcher v2")
        st.caption(
            "上传 PDF、粘贴 URL 或描述研究方向。\n\n"
            "**快速开始：** 点击右上角 **🔔 演示** → 📄 模拟解析 PDF"
        )
        return

    for msg in msgs:
        role = msg["role"]
        content = msg.get("content", "")
        tool_lines = msg.get("tool_lines", [])

        if role == "user":
            st.markdown(
                f'<div style="color:#888;font-size:0.8em;margin-bottom:2px">You</div>'
                f'<div style="margin-bottom:12px">{_escape_html(content)}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="color:#4fc3f7;font-size:0.8em;margin-bottom:2px">Assistant</div>',
                unsafe_allow_html=True,
            )

            for tl in tool_lines:
                _render_tool_line(tl)

            if content:
                st.markdown(
                    f'<div style="margin-bottom:12px;white-space:pre-wrap">{_escape_html(content)}</div>',
                    unsafe_allow_html=True,
                )


def _render_tool_line(tl: dict) -> None:
    status = tl.get("status", "pending")
    text = tl.get("text", "")
    duration = tl.get("duration", "")
    kind = tl.get("kind", "info")

    icon_map = {"pending": "○", "running": "●", "done": "✓", "error": "✗", "info": "ℹ"}
    css_map = {
        "pending": "tool-pending",
        "running": "tool-pending",
        "done": "tool-done",
        "error": "tool-error",
        "info": "tool-info",
    }

    icon = icon_map.get(status, "○")
    css_cls = css_map.get(status, "tool-pending")
    dur_str = f" ({duration})" if duration else ""

    st.markdown(
        f'<div class="tool-line {css_cls}">{icon} {text}{dur_str}</div>',
        unsafe_allow_html=True,
    )


def _handle_user_input(user_input: str) -> None:
    user_input = user_input.strip()
    if not user_input:
        return

    st.session_state.v2_messages.append({"role": "user", "content": user_input})

    reply = _simulate_reply(user_input)

    st.session_state.v2_messages.append({"role": "assistant", "content": reply})
    st.rerun()


def _render_plus_popover() -> None:
    with st.popover("➕", use_container_width=False):
        st.caption("**上传文件**")
        uploaded = st.file_uploader(
            "支持 PDF / txt / md",
            type=["pdf", "txt", "md", "markdown"],
            label_visibility="collapsed",
            key="v2_file_uploader",
        )
        if uploaded:
            _simulate_parse_flow(uploaded.name)
            st.rerun()

        st.divider()
        st.caption("**或粘贴链接**")
        url = st.text_input(
            "arXiv / GitHub / 网页 URL",
            placeholder="https://…",
            label_visibility="collapsed",
            key="v2_url_input",
        )
        if st.button("添加链接", key="v2_add_url_btn", use_container_width=True) and url.strip():
            _simulate_url_flow(url.strip())
            st.rerun()

        st.divider()
        if st.button("🔍 搜索论文", key="v2_search_btn", use_container_width=True):
            _simulate_search_flow()
            st.rerun()


# ── Simulation flows ──

def _simulate_parse_flow(label: str) -> None:
    st.session_state.v2_messages.append({"role": "user", "content": f"📎 {label}"})

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    msg = {
        "role": "assistant",
        "content": "",
        "tool_lines": [
            {"status": "running", "text": f"解析 PDF… (mineru_pipeline_v1) · {label[:30]}", "kind": "info"},
        ],
    }
    st.session_state.v2_messages.append(msg)
    show_toast(f"开始解析 {label[:30]}", kind="info")

    time.sleep(1.8)

    msg["tool_lines"][0] = {"status": "done", "text": f"解析完成 · {label[:30]}", "kind": "success", "duration": "2.1s"}
    msg["content"] = (
        "已解析论文。基于 paper_summary：\n\n"
        "**SimpleNet** (arXiv 2303.15140v2) 提出 Feature Adapter——在预训练 backbone 后插入轻量级适配层，"
        "将特征映射到异常检测空间。\n\n"
        "**可迁移性**：Feature Adapter 可在不改变 PatchCore memory bank / coreset / scoring 的前提下接入。"
    )
    notify_parse_complete_v2(label[:30])


def _simulate_url_flow(url: str) -> None:
    is_github = "github.com" in url.lower()
    st.session_state.v2_messages.append({"role": "user", "content": f"🔗 {url}"})

    if is_github:
        repo = url.split("/")[-1] or "repo"
        msg = {
            "role": "assistant",
            "content": "",
            "tool_lines": [
                {"status": "running", "text": f"clone 仓库… (git_clone) · {repo}", "kind": "info"},
            ],
        }
        st.session_state.v2_messages.append(msg)
        show_toast(f"开始 clone {repo}", kind="info")

        time.sleep(2.0)
        msg["tool_lines"][0] = {"status": "done", "text": f"clone 完成 · {repo}", "kind": "success", "duration": "3.4s"}
        msg["tool_lines"].append({"status": "running", "text": "分析仓库… (repo_reader)", "kind": "info"})

        time.sleep(1.0)
        msg["tool_lines"][1] = {"status": "done", "text": "分析完成", "kind": "success", "duration": "1.1s"}
        msg["content"] = (
            f"**{repo}** clone 完成。repo_map.md 已生成：\n\n"
            f"- `patchcore/patchcore.py` — 主流程\n"
            f"- `patchcore/backbones.py` — 特征提取器注册\n"
            f"- `patchcore/common.py` — 数据集加载\n"
            f"- `patchcore/sampler.py` — coreset 采样\n\n"
            "关键接口：`PatchCore.fit(train_loader)`, `PatchCore.predict(test_loader)`"
        )
        notify_clone_complete_v2(repo)
    else:
        msg = {
            "role": "assistant",
            "content": "",
            "tool_lines": [
                {"status": "running", "text": f"下载网页… (web_fetch) · {url[:40]}", "kind": "info"},
            ],
        }
        st.session_state.v2_messages.append(msg)
        show_toast(f"开始下载 {url[:30]}", kind="info")

        time.sleep(1.5)
        msg["tool_lines"][0] = {"status": "done", "text": f"下载完成 · {url[:40]}", "kind": "success", "duration": "1.8s"}
        msg["tool_lines"].append({"status": "running", "text": "解析内容… (paper_reader)", "kind": "info"})

        time.sleep(1.2)
        msg["tool_lines"][1] = {"status": "done", "text": "解析完成", "kind": "success", "duration": "1.6s"}
        msg["content"] = (
            f"URL 内容已下载并解析。paper_summary.md 已生成。\n\n"
            "你可以问我论文内容、可迁移方法或研究方案。"
        )
        notify_parse_complete_v2(url[:30])


def _simulate_search_flow() -> None:
    st.session_state.v2_messages.append({"role": "user", "content": "搜索 MVTec AD 最新方法"})

    msg = {
        "role": "assistant",
        "content": "",
        "tool_lines": [
            {"status": "running", "text": "搜索中… (web_search)", "kind": "info"},
        ],
    }
    st.session_state.v2_messages.append(msg)
    show_toast("搜索 MVTec AD 最新方法", kind="info")

    time.sleep(1.0)
    msg["tool_lines"][0] = {"status": "done", "text": "搜索完成 · 5 个候选来源", "kind": "info", "duration": "0.8s"}
    msg["content"] = (
        "找到 5 个候选来源（candidate_source_only）：\n\n"
        "1. DINOv2 + PatchCore — GitHub\n"
        "2. EfficientAD — arXiv 2303.05165\n"
        "3. Anomalib — GitHub\n"
        "4. PatchCore 原文 — arXiv 2106.08265\n"
        "5. FastRecon — arXiv 2304.05189"
    )
    notify_search_complete_v2(5)


def _simulate_reply(user_input: str) -> str:
    if "http" in user_input:
        return "已接收链接，后台处理中。完成后弹出通知。"
    if "搜索" in user_input or "最新" in user_input:
        return "搜索中…完成后弹出通知。"
    return (
        "收到。当前暂无已解析的资料。\n\n"
        "试试点击右上角 **🔔 演示** → 📄 模拟解析 PDF。"
    )


# ── Helpers ──

def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def notify_parse_complete_v2(label: str) -> None:
    show_toast(f"解析完成 · {label}", kind="success")


def notify_clone_complete_v2(repo: str) -> None:
    show_toast(f"clone 完成 · {repo}", kind="success")


def notify_search_complete_v2(count: int) -> None:
    show_toast(f"找到 {count} 个候选来源", kind="info")
