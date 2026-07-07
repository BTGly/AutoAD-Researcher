"""API Key 配置 — 首次弹窗，持久化到 ~/.autoad/config.json。

API key 跟着设备走，不跟着项目走。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

CONFIG_PATH = Path.home() / ".autoad" / "config.json"
DEFAULT_PROVIDER = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


def _load_config() -> dict[str, Any]:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_api_key() -> str:
    cfg = _load_config()
    return cfg.get("api_key", "") or os.environ.get("DEEPSEEK_API_KEY", "")


def get_provider_url() -> str:
    return _load_config().get("provider_url", DEFAULT_PROVIDER)


def get_model() -> str:
    return _load_config().get("model", DEFAULT_MODEL)


def ensure_api_key_configured() -> None:
    if st.session_state.api_key_configured:
        return

    api_key = get_api_key()
    if api_key:
        st.session_state.api_key_configured = True
        st.session_state.api_key = api_key
        return

    st.markdown("## 🔑 配置 API Key")
    st.caption("API Key 保存在本地设备，不会上传。")

    key = st.text_input("API Key", type="password", placeholder="sk-…")
    url = st.text_input("Base URL", value=DEFAULT_PROVIDER)
    model = st.text_input("Model", value=DEFAULT_MODEL)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("保存并开始", type="primary", use_container_width=True):
            if not key.strip():
                st.error("请填写 API Key。")
            else:
                _save_config({"api_key": key.strip(), "provider_url": url.strip(), "model": model.strip()})
                st.session_state.api_key_configured = True
                st.session_state.api_key = key.strip()
                st.rerun()
    with col2:
        if st.button("跳过（使用环境变量 DEEPSEEK_API_KEY）", type="secondary", use_container_width=True):
            env_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not env_key:
                st.error("未设置 DEEPSEEK_API_KEY 环境变量。")
            else:
                st.session_state.api_key_configured = True
                st.session_state.api_key = env_key
                st.rerun()


def render_config_button() -> None:
    with st.popover("⚙️"):
        cfg = _load_config()
        st.caption(f"Provider: {cfg.get('provider_url', DEFAULT_PROVIDER)}")
        st.caption(f"Model: {cfg.get('model', DEFAULT_MODEL)}")
        st.caption(f"API Key: {'sk-••••' + cfg.get('api_key', '')[-4:] if cfg.get('api_key') else '未配置'}")

        new_key = st.text_input("新 API Key", type="password", key="_new_api_key")
        new_url = st.text_input("新 Base URL", value=cfg.get("provider_url", DEFAULT_PROVIDER), key="_new_provider_url")
        new_model = st.text_input("新 Model", value=cfg.get("model", DEFAULT_MODEL), key="_new_model")
        if st.button("更新配置", type="primary"):
            _save_config({"api_key": new_key.strip(), "provider_url": new_url.strip(), "model": new_model.strip()})
            st.session_state.api_key = new_key.strip()
            st.success("已更新。")
            st.rerun()
