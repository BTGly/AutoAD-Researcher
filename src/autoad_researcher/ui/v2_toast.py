"""Toast 通知 — 右上角弹窗，3 秒消失。

使用方法：
    show_toast("解析完成", kind="success")

仅通过 st.toast() 的 icon 参数控制图标，
不在消息文本里重复加 emoji，避免重复。
"""

from __future__ import annotations

import streamlit as st

_ICON_MAP = {
    "success": "✅",
    "error": "❌",
    "info": "ℹ️",
    "warning": "⚠️",
}


def show_toast(message: str, kind: str = "success") -> None:
    icon = _ICON_MAP.get(kind, "")
    st.toast(message, icon=icon)


def notify_parse_complete(source_label: str, attempt_id: str | None = None) -> None:
    show_toast(f"解析完成 · {source_label}", kind="success")


def notify_clone_complete(repo_name: str) -> None:
    show_toast(f"clone 完成 · {repo_name}", kind="success")


def notify_search_complete(count: int) -> None:
    show_toast(f"找到 {count} 个候选来源", kind="info")


def notify_parse_failed(source_label: str, reason: str = "") -> None:
    msg = f"解析失败 · {source_label}"
    if reason:
        msg += f"：{reason}"
    show_toast(msg, kind="error")


def notify_clone_failed(repo_url: str, reason: str = "") -> None:
    msg = f"clone 失败 · {repo_url}"
    if reason:
        msg += f"：{reason}"
    show_toast(msg, kind="error")
