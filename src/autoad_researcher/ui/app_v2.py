"""AutoAD Researcher v2 — Claude Code / OpenCode / MiMoCode 风格前端。

首次访问：API Key 配置弹窗。
后续访问：直接进入聊天界面。
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="AutoAD Researcher",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": None,
    },
)

from autoad_researcher.ui.v2_config import ensure_api_key_configured, render_config_button
from autoad_researcher.ui.v2_chat import (
    _simulate_parse_flow,
    _simulate_search_flow,
    _simulate_url_flow,
    render_chat_page,
)
from autoad_researcher.ui.v2_toast import show_toast


def main():
    if "api_key_configured" not in st.session_state:
        st.session_state.api_key_configured = False

    ensure_api_key_configured()

    if not st.session_state.api_key_configured:
        st.stop()

    col_title, col_demo, col_config = st.columns([4, 2, 1])
    with col_title:
        st.markdown("### AutoAD Researcher v2")
    with col_demo:
        with st.popover("🔔 演示（点一下看完整模拟）"):
            st.caption("**Subagent 演示**")
            if st.button("📄 模拟解析 PDF", key="_demo_parse_pdf", use_container_width=True):
                _simulate_parse_flow("2303.15140v2.pdf")
                st.rerun()
            if st.button("🔗 模拟下载 arXiv URL", key="_demo_url", use_container_width=True):
                _simulate_url_flow("https://arxiv.org/abs/2303.15140")
                st.rerun()
            if st.button("📦 模拟 clone GitHub 仓库", key="_demo_clone_repo", use_container_width=True):
                _simulate_url_flow("https://github.com/amazon-science/patchcore-inspection")
                st.rerun()
            if st.button("🔍 模拟搜索论文", key="_demo_search_papers", use_container_width=True):
                _simulate_search_flow()
                st.rerun()
            st.divider()
            st.caption("**Toast 通知演示**")
            if st.button("✅ 成功 toast", key="_demo_toast_ok", use_container_width=True):
                show_toast("PDF 解析完成 · paper_summary.md 已生成", kind="success")
            if st.button("❌ 失败 toast", key="_demo_toast_fail", use_container_width=True):
                show_toast("PDF 解析失败 · 文件可能为扫描件", kind="error")
    with col_config:
        render_config_button()

    render_chat_page()


if __name__ == "__main__":
    main()
