"""底部状态栏 — Claude Code 风格的紧凑信息条。

Sources: 3 (2 parsed, 1 pending) │ Evidence: 2 usable │ Draft: Ready
"""

from __future__ import annotations

import streamlit as st


def render_status_bar(
    *,
    total_sources: int = 0,
    parsed_sources: int = 0,
    pending_jobs: int = 0,
    usable_evidence: int = 0,
    candidate_only: int = 0,
    draft_ready: bool = False,
    frozen: bool = False,
) -> None:
    parts = []

    if total_sources:
        parts.append(f"📄 Sources: {total_sources} ({parsed_sources} parsed, {pending_jobs} pending)")
    if usable_evidence or candidate_only:
        parts.append(f"🔬 Evidence: {usable_evidence} usable, {candidate_only} candidate-only")
    if draft_ready:
        parts.append("📝 Draft: Ready")
    if frozen:
        parts.append("🔒 Frozen")

    if not parts:
        parts.append("尚无资料。输入问题开始…")

    st.caption("  │  ".join(parts))
