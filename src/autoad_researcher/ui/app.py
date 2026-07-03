"""Streamlit UI — Phase 1: 制品浏览器 + 预检执行器。"""

import os
import sys
import hashlib
from datetime import datetime, timezone

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from autoad_researcher.ui.artifact_viewer import (
    list_stage_dirs,
    get_execution_manifest,
    get_runner_intake_report,
    get_gpu_evidence,
    get_final_facts,
    get_final_report_md,
    get_events_tail,
    get_artifact_chain,
    list_artifact_files,
    summarize_final_status,
    run_dir_path,
)
from autoad_researcher.ui.run_commands import run_preflight

st.set_page_config(
    page_title="AutoAD Researcher — L3 控制台",
    page_icon="🔬",
    layout="wide",
)

_DEFAULTS = {
    "dataset_root": "/root/autodl-tmp/mvtec",
    "provider_base_url": "https://api.deepseek.com",
    "mode": "l3-preflight",
    "api_key": "",
    "preflight_result": None,
    "preflight_running": False,
    "_run_id_hash": "",
}
for k, v in _DEFAULTS.items():
    st.session_state.setdefault(k, v)

def _generate_run_id() -> str:
    """每次页面渲染生成稳定的 run_id：时间戳 + 短哈希。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    h = hashlib.md5(ts.encode()).hexdigest()[:4]
    return f"run_{ts}_{h}"

if not st.session_state._run_id_hash:
    st.session_state._run_id_hash = _generate_run_id()

PAGES = [
    "1. 运行配置",
    "2. 预检执行器",
    "3. 制品浏览器",
    "4. 执行监控",
    "5. 最终审阅",
]
page = st.sidebar.radio("页面导航", PAGES, index=0)

st.sidebar.markdown("---")
st.sidebar.caption(f"运行 ID: `{st.session_state._run_id_hash}`")
st.sidebar.markdown("---")
old_run = st.sidebar.text_input("浏览已有运行", placeholder="run_l3_bottle_001", key="_old_run_id")
if old_run:
    st.session_state._browse_run_id = old_run
else:
    st.session_state._browse_run_id = st.session_state._run_id_hash
st.sidebar.caption(f"浏览: `{st.session_state._browse_run_id}`")

# ═══════════════════════════════════════════════════════════════════════════
# 页面 1: 运行配置
# ═══════════════════════════════════════════════════════════════════════════
if page == "1. 运行配置":
    st.title("运行配置")
    st.caption("填写 API Key 即可开始 — 其他参数已自动配置。")

    # ── API Key 核心区 ──────────────────────────────────────────────────
    api_key_val = st.text_input(
        "DeepSeek API Key",
        type="password",
        key="api_key",
        placeholder="sk-…",
    )
    if api_key_val:
        st.success("✅ API Key 已注入")
    else:
        st.info("请在下方输入 API Key，按回车确认")

    # ── 自动生成的 Run ID ───────────────────────────────────────────────
    st.markdown("---")
    run_col, refresh_col = st.columns([3, 1])
    with run_col:
        st.text_input("运行 ID（自动生成）", value=st.session_state._run_id_hash, disabled=True)
        st.caption("每次刷新页面会生成新的 ID，旧 ID 对应的制品不会丢失。")
    with refresh_col:
        if st.button("🔄 重新生成"):
            st.session_state._run_id_hash = _generate_run_id()
            st.rerun()

    # ── 高级配置（折叠）──────────────────────────────────────────────────
    with st.expander("高级配置（通常无需修改）"):
        st.text_input("数据集根目录", key="dataset_root")
        st.text_input("Provider 接口地址", key="provider_base_url")
        st.selectbox("模式", ["l3-preflight"], key="mode", disabled=True,
                     help="UI 仅支持预检模式。真实 L3 请执行下方命令。")

    # ── 等效命令行（折叠）────────────────────────────────────────────────
    with st.expander("等效命令行（仅展示）"):
        cmd = (
            f"uv run autoad stage3-acceptance "
            f"--run-id {st.session_state._run_id_hash} "
            f"--mode {st.session_state.mode} "
            f"--provider-base-url {st.session_state.provider_base_url} "
            f"--json"
        )
        st.code(cmd, language="bash")

# ═══════════════════════════════════════════════════════════════════════════
# 页面 2: 预检执行器
# ═══════════════════════════════════════════════════════════════════════════
elif page == "2. 预检执行器":
    st.title("预检执行器")
    st.caption("执行 `stage3-acceptance --mode l3-preflight`（不会调用 LLM，不会真实执行 GPU）。")

    if not st.session_state.api_key:
        st.warning("请先在「运行配置」中填写 API Key。")

    col1, col2 = st.columns([1, 3])
    with col1:
        run_btn = st.button(
            "执行预检",
            disabled=not st.session_state.api_key or st.session_state.preflight_running,
            type="primary",
        )
    with col2:
        if st.session_state.preflight_running:
            st.info("正在执行预检…（最长约 60 秒）")

    if run_btn:
        st.session_state.preflight_running = True
        st.session_state.preflight_result = None
        with st.spinner("正在执行预检…"):
            result = run_preflight(
                run_id=st.session_state._run_id_hash,
                provider_base_url=st.session_state.provider_base_url,
                api_key=st.session_state.api_key,
                dataset_root=st.session_state.dataset_root,
            )
        st.session_state.preflight_result = result
        st.session_state.preflight_running = False
        st.rerun()

    if st.session_state.preflight_result:
        result = st.session_state.preflight_result
        wrapper_status = result.get("status", "unknown")
        orch_status = result.get("status", None) if wrapper_status not in ("subprocess_failed", "timeout", "error") else None
        failure_reason = result.get("failure_reason", "")

        if wrapper_status == "subprocess_failed":
            st.error("子进程返回错误", icon="❌")
            with st.expander("查看详细输出"):
                if result.get("stderr"):
                    st.text("stderr:")
                    st.code(result["stderr"], language="text")
                if result.get("stdout"):
                    st.text("stdout:")
                    st.code(result["stdout"], language="json")
        elif wrapper_status == "timeout":
            st.error("预检超时（超过 300 秒）", icon="⏰")
        elif wrapper_status == "error":
            st.error(f"执行异常：{result.get('error')}", icon="❌")
        elif "blocked_l3_preflight_missing" in failure_reason:
            missing_items = failure_reason.replace("blocked_l3_preflight_missing: ", "")
            st.warning(f"预检未通过 — 缺少环境变量：**{missing_items}**", icon="⚠️")
            st.caption("请确认 API Key 已填写且 Streamlit 已拉取最新代码后重试。")
            with st.expander("查看原始结果"):
                st.json(result)
        elif "blocked_l3_real_run_deferred_preflight_only" in failure_reason:
            st.success("预检通过，环境就绪", icon="✅")
            st.info("当前为预检模式，不会执行真实管线。若要运行真实 L3，请在终端手动执行下方命令。")
            st.metric("运行 ID", result.get("run_id", "—"))
            st.metric("制品目录", result.get("artifact_dir", "—"))
            with st.expander("查看原始结果"):
                st.json(result)
        else:
            st.info("预检完成", icon="✅")
            st.metric("运行 ID", result.get("run_id", "—"))
            st.metric("制品目录", result.get("artifact_dir", "—"))
            with st.expander("查看原始结果"):
                st.json(result)

    if not st.session_state.preflight_result and not run_btn:
        st.info("点击 **执行预检** 开始。")

    st.markdown("---")
    st.subheader("真实 L3 执行")
    st.warning("本 UI 不会触发真实 L3 执行。请在终端手动运行：")
    real_cmd = (
        f"AUTOAD_L3_REAL_EXECUTION_ALLOWED=1 \\\n"
        f"uv run autoad stage3-acceptance "
        f"--run-id {st.session_state._run_id_hash} "
        f"--mode {st.session_state.mode} "
        f"--provider-base-url {st.session_state.provider_base_url} "
        f"--json"
    )
    st.code(real_cmd, language="bash")

# ═══════════════════════════════════════════════════════════════════════════
# 页面 3: 制品浏览器
# ═══════════════════════════════════════════════════════════════════════════
elif page == "3. 制品浏览器":
    st.title("制品浏览器")
    try:
        run_dir = run_dir_path("runs", st.session_state._browse_run_id)
    except ValueError as exc:
        st.error(f"无效的 run_id: {exc}")
        run_dir = None

    if run_dir is None or not run_dir.is_dir():
        st.warning(f"运行目录未找到: `{run_dir}`")
    else:
        stages = list_stage_dirs(run_dir)
        for s in stages:
            with st.expander(f"{'✅' if s['exists'] else '⏳'} **{s['name']}**", expanded=s['exists']):
                if not s['exists']:
                    st.caption("尚未生成。")
                else:
                    files = list_artifact_files(run_dir, s['name'])
                    if files:
                        rows = [{"文件名": f["name"], "大小": f"{f['size']:,} B", "路径": f["path"]} for f in files]
                        st.dataframe(rows, use_container_width=True)
                    else:
                        st.caption("空目录。")

# ═══════════════════════════════════════════════════════════════════════════
# 页面 4: 执行监控
# ═══════════════════════════════════════════════════════════════════════════
elif page == "4. 执行监控":
    st.title("执行监控")
    try:
        run_dir = run_dir_path("runs", st.session_state._browse_run_id)
    except ValueError as exc:
        st.error(f"无效的 run_id: {exc}")
        run_dir = None

    if run_dir is None or not run_dir.is_dir():
        st.warning(f"运行目录未找到: `{run_dir}`")
    else:
        col_refresh, _ = st.columns([1, 5])
        with col_refresh:
            st.button("刷新", key="_refresh_monitor")

        manifest = get_execution_manifest(run_dir)
        intake = get_runner_intake_report(run_dir)
        gpu = get_gpu_evidence(run_dir)

        tabs = st.tabs(["执行清单", "准入报告", "GPU 证据", "事件日志"])
        with tabs[0]:
            if manifest:
                st.json(manifest)
            else:
                st.caption("尚未生成。")
        with tabs[1]:
            if intake:
                st.json(intake)
            else:
                st.caption("尚未生成。")
        with tabs[2]:
            if gpu:
                st.json(gpu)
            else:
                st.caption("尚未生成。")
        with tabs[3]:
            events = get_events_tail(run_dir)
            if events:
                st.code("\n".join(events), language="json")
            else:
                st.caption("未找到事件记录。")

# ═══════════════════════════════════════════════════════════════════════════
# 页面 5: 最终审阅
# ═══════════════════════════════════════════════════════════════════════════
elif page == "5. 最终审阅":
    st.title("最终审阅")
    try:
        run_dir = run_dir_path("runs", st.session_state._browse_run_id)
    except ValueError as exc:
        st.error(f"无效的 run_id: {exc}")
        run_dir = None

    if run_dir is None or not run_dir.is_dir():
        st.warning(f"运行目录未找到: `{run_dir}`")
    else:
        col_refresh, _ = st.columns([1, 5])
        with col_refresh:
            st.button("刷新", key="_refresh_review")

        final_facts = get_final_facts(run_dir)
        manifest = get_execution_manifest(run_dir)
        summary = summarize_final_status(final_facts, manifest)

        col1, col2, col3 = st.columns(3)

        with col1:
            eng = summary["engineering_success"]
            if eng is True:
                st.success("✅ 工程管线\n管线完成，真实补丁已应用")
            elif eng is False:
                st.error("❌ 工程管线\n空补丁或管线未完成")
            else:
                st.info("⏳ 工程管线\n数据尚未生成")

        with col2:
            exc = summary["execution_success"]
            if exc is True:
                st.success("✅ GPU 执行\nGPU 已验证，3/0/0 单元")
            elif exc is False:
                st.error("❌ GPU 执行\n未验证或单元失败")
            else:
                st.info("⏳ GPU 执行\n数据尚未生成")

        with col3:
            sci = summary["scientific_success"]
            claim = summary["scientific_claim"] or "—"
            if sci is True:
                st.success(f"✅ 科学改进\n已证明改进\n({claim})")
            elif sci is False:
                st.error(f"❌ 科学改进\n未证明改进\n({claim})")
            else:
                st.info(f"⏳ 科学改进\n数据尚未生成\n({claim})")

        st.markdown("---")
        st.subheader("制品链")
        chain = get_artifact_chain(run_dir)
        rows = []
        for c in chain:
            icon = "✅" if c["handoff_sha"] != "—" and c["exists"] else "⏳"
            rows.append({"阶段": icon + " " + c["stage"], "handoff SHA": c["handoff_sha"]})
        st.dataframe(rows, use_container_width=True)

        st.markdown("---")
        st.subheader("最终报告事实")
        if final_facts:
            st.json(final_facts)
        else:
            st.caption("尚未生成。")

        st.subheader("最终报告 (Markdown)")
        md = get_final_report_md(run_dir)
        if md:
            st.markdown(md)
        else:
            st.caption("尚未生成。")
