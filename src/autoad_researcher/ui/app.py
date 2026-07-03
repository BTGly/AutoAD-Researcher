"""Streamlit UI — Phase 1: 制品浏览器 + 预检执行器。"""

import hashlib
import os
import sys
from datetime import datetime, timezone

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from autoad_researcher.ui.artifact_viewer import (
    STAGE_DESCRIPTIONS,
    RECOMMENDED_FILES,
    get_artifact_chain,
    get_events_tail,
    get_execution_manifest,
    get_final_facts,
    get_final_report_md,
    get_gpu_evidence,
    get_runner_intake_report,
    list_artifact_files,
    list_stage_dirs,
    run_dir_path,
    summarize_final_status,
)
from autoad_researcher.ui.run_commands import run_preflight

_API_KEY_WIDGET_KEY = "_api_key_widget"
_API_KEY_STATE_KEY = "_api_key_raw"

st.set_page_config(
    page_title="AutoAD Researcher — L3 控制台",
    page_icon="🔬",
    layout="wide",
)

_DEFAULTS = {
    "dataset_root": "/root/autodl-tmp/mvtec",
    "provider_base_url": "https://api.deepseek.com",
    "mode": "l3-preflight",
    "preflight_result": None,
    "preflight_running": False,
    "_run_id_hash": "",
}
for k, v in _DEFAULTS.items():
    st.session_state.setdefault(k, v)


def _generate_run_id() -> str:
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
old_run = st.sidebar.text_input("浏览已有运行", placeholder="run_l3_bottle_001", key="_old_run_id")
st.session_state._browse_run_id = old_run or st.session_state._run_id_hash
st.sidebar.caption(f"浏览: `{st.session_state._browse_run_id}`")


# ═══════════════════════════════════════════════════════════════════════════
# 页面 1: 运行配置
# ═══════════════════════════════════════════════════════════════════════════
if page == "1. 运行配置":
    st.title("运行配置")
    st.info("你只需要填写 API Key 即可运行预检。系统会自动生成运行 ID 并配置好其他参数。")

    api_key_val = st.text_input(
        "DeepSeek API Key",
        type="password",
        key=_API_KEY_WIDGET_KEY,
        placeholder="sk-…",
    )
    if api_key_val:
        st.session_state[_API_KEY_STATE_KEY] = api_key_val
        st.success("✅ API Key 已注入 — 仅保存于本次会话内存，不会写入磁盘")
    elif st.session_state.get(_API_KEY_STATE_KEY):
        st.success("✅ API Key 已保留 — 仅保存于本次会话内存，不会写入磁盘")
    else:
        st.info("请输入 API Key，按回车确认")

    if st.session_state.get(_API_KEY_STATE_KEY):
        if st.button("清除 API Key"):
            st.session_state.pop(_API_KEY_STATE_KEY, None)
            st.session_state.pop(_API_KEY_WIDGET_KEY, None)
            st.rerun()

    st.markdown("---")
    run_col, refresh_col = st.columns([3, 1])
    with run_col:
        st.text_input("运行 ID（自动生成）", value=st.session_state._run_id_hash, disabled=True)
        st.caption("首次打开页面自动生成运行 ID。点击「重新生成」可创建新的 ID。旧 ID 的制品不会丢失。")
    with refresh_col:
        if st.button("🔄 重新生成"):
            st.session_state._run_id_hash = _generate_run_id()
            st.rerun()

    with st.expander("高级配置（通常无需修改）"):
        st.text_input("数据集根目录", key="dataset_root")
        st.text_input("Provider 接口地址", key="provider_base_url")
        st.selectbox("模式", ["l3-preflight"], key="mode", disabled=True)

    with st.expander("终端复现命令"):
        real_cmd = (
            f"export AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT=\"{st.session_state.dataset_root}\"\n"
            f"read -s -p \"DeepSeek API key: \" DEEPSEEK_API_KEY\n"
            f"export DEEPSEEK_API_KEY\n\n"
            f"uv run autoad stage3-acceptance \\\n"
            f"  --run-id {st.session_state._run_id_hash} \\\n"
            f"  --mode {st.session_state.mode} \\\n"
            f"  --provider-base-url \"{st.session_state.provider_base_url}\" \\\n"
            f"  --json"
        )
        st.code(real_cmd, language="bash")

# ═══════════════════════════════════════════════════════════════════════════
# 页面 2: 预检执行器
# ═══════════════════════════════════════════════════════════════════════════
elif page == "2. 预检执行器":
    st.title("预检执行器")
    st.info(
        "预检不会请求模型生成内容，也不会执行 GPU benchmark。"
        "它只检查运行所需配置是否齐全：API Key 是否存在、数据集路径是否可达等。"
        "执行预检**不消耗 token**，可以反复运行。"
    )

    st.markdown("**预检后你需要做什么：**")
    st.caption("预检通过 → 复制「终端复现命令」到 SSH 终端执行真实 L3 → 回到「执行监控」和「最终审阅」查看结果")

    api_key = st.session_state.get(_API_KEY_STATE_KEY, "")
    if not api_key:
        st.warning("请先在「运行配置」中填写 API Key。")

    col1, col2 = st.columns([1, 3])
    with col1:
        run_btn = st.button(
            "执行预检",
            disabled=not api_key or st.session_state.preflight_running,
            type="primary",
        )
    with col2:
        if st.session_state.preflight_running:
            st.info("正在执行预检…（约 5-10 秒）")

    if run_btn:
        st.session_state.preflight_running = True
        st.session_state.preflight_result = None
        with st.spinner("正在执行预检…"):
            result = run_preflight(
                run_id=st.session_state._run_id_hash,
                provider_base_url=st.session_state.provider_base_url,
                api_key=api_key,
                dataset_root=st.session_state.dataset_root,
            )
        st.session_state.preflight_result = result
        st.session_state.preflight_running = False
        st.rerun()

    if st.session_state.preflight_result:
        result = st.session_state.preflight_result
        wrapper_status = result.get("status", "unknown")
        failure_reason = result.get("failure_reason", "")

        if wrapper_status == "subprocess_failed":
            st.error("子进程返回错误", icon="❌")
            with st.expander("查看详细输出"):
                if result.get("stderr"):
                    st.code(result["stderr"], language="text")
                if result.get("stdout"):
                    st.code(result["stdout"], language="json")
        elif wrapper_status == "timeout":
            st.error("预检超时（超过 300 秒）", icon="⏰")
        elif wrapper_status == "error":
            st.error(f"执行异常：{result.get('error')}", icon="❌")
        elif "blocked_l3_preflight_missing" in failure_reason:
            missing_items = failure_reason.replace("blocked_l3_preflight_missing: ", "")
            st.warning(f"预检未通过 — 缺少：**{missing_items}**", icon="⚠️")
            st.caption("请确认 API Key 已填写。如果已填写但仍失败，按 Ctrl+C 重启 Streamlit 后重试。")
            with st.expander("查看原始结果"):
                st.json(result)
        elif "blocked_l3_real_run_deferred_preflight_only" in failure_reason:
            st.success("✅ 预检通过 — 环境已就绪", icon="✅")

            checklist_col1, checklist_col2 = st.columns(2)
            with checklist_col1:
                st.markdown("**✅ 已完成**")
                st.markdown("- 环境配置检查通过")
                st.markdown("- API Key 已配置")
                st.markdown("- Provider 连接就绪")
            with checklist_col2:
                st.markdown("**⏳ 待执行**")
                st.markdown("- 真实 L3 管线（patch-plan → final-report）")
                st.markdown("- GPU benchmark 实验")
                st.markdown("- 结果分析和最终报告")

            st.markdown("**下一步操作：**")
            st.markdown("1. 复制下方的「终端复现命令」到 SSH 终端执行")
            st.markdown("2. 执行完成后回到「执行监控」查看实验完成情况")
            st.markdown("3. 最后进入「最终审阅」查看三层结论")

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
    st.subheader("终端复现命令（真实 L3 执行）")
    st.warning(
        "执行此命令会调用 LLM、修改 patchcore 工作区并运行 GPU benchmark。"
        "请确认仓库干净、数据集路径正确后再执行。"
    )
    real_cmd = (
        f"export AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT=\"{st.session_state.dataset_root}\"\n"
        f"read -s -p \"DeepSeek API key: \" DEEPSEEK_API_KEY\n"
        f"export DEEPSEEK_API_KEY\n\n"
        f"AUTOAD_L3_REAL_EXECUTION_ALLOWED=1 \\\n"
        f"uv run autoad stage3-acceptance \\\n"
        f"  --run-id {st.session_state._run_id_hash} \\\n"
        f"  --mode {st.session_state.mode} \\\n"
        f"  --provider-base-url \"{st.session_state.provider_base_url}\" \\\n"
        f"  --json"
    )
    st.code(real_cmd, language="bash")

# ═══════════════════════════════════════════════════════════════════════════
# 页面 3: 制品浏览器
# ═══════════════════════════════════════════════════════════════════════════
elif page == "3. 制品浏览器":
    st.title("制品浏览器")
    st.info("每个阶段会生成对应的产物文件。阶段按执行顺序排列，已生成的标 ✅，未生成的标 ⏳。")

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
            desc = s.get("description", "")
            recommended = s.get("recommended", [])
            header = f"{'✅' if s['exists'] else '⏳'} **{s['name']}**"
            if desc:
                header += f" — {desc}"

            with st.expander(header, expanded=s['exists']):
                if not s['exists']:
                    st.caption("尚未生成。")
                else:
                    files = list_artifact_files(run_dir, s['name'])
                    if files:
                        rows = []
                        for f in files:
                            is_rec = "⭐ " if f["name"] in recommended else ""
                            rows.append({"文件名": is_rec + f["name"], "大小": f"{f['size']:,} B", "路径": f["path"]})
                        st.dataframe(rows, use_container_width=True)
                    else:
                        st.caption("空目录。")

# ═══════════════════════════════════════════════════════════════════════════
# 页面 4: 执行监控
# ═══════════════════════════════════════════════════════════════════════════
elif page == "4. 执行监控":
    st.title("执行监控")
    st.info("查看实验执行的实时状态 — 包括基线/变体实验完成情况、GPU 证据、准入报告和事件日志。")

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

        # ── Summary cards ─────────────────────────────────────────────────
        if manifest:
            st.subheader("执行摘要")
            tot = manifest.get("total_unit_count") or len(manifest.get("unit_records", []))
            completed = manifest.get("completed_unit_count", 0)
            failed = manifest.get("failed_unit_count", 0)
            blocked = manifest.get("blocked_unit_count", 0)

            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.metric("总单元数", tot if tot else len(manifest.get("unit_records", [])))
            with m2:
                st.metric("完成", completed)
            with m3:
                st.metric("失败", failed)
            with m4:
                st.metric("阻塞", blocked)
            if completed == tot and tot > 0 and failed == 0:
                st.success("✅ 所有实验单元已完成")
            elif completed > 0:
                st.info(f"⏳ {completed}/{tot} 已完成")
            with st.expander("查看原始执行清单"):
                st.json(manifest)

        if gpu:
            st.subheader("GPU 证据")
            device = gpu.get("device_name") or gpu.get("gpu_name", "未知")
            used = gpu.get("gpu_used", False)
            source = gpu.get("source", "未知")
            g1, g2, g3 = st.columns(3)
            with g1:
                st.metric("GPU 状态", "✅ 已验证" if used else "❌ 未使用")
            with g2:
                st.metric("设备", device)
            with g3:
                st.metric("检测来源", source)
            with st.expander("查看原始 GPU 证据"):
                st.json(gpu)

        if intake:
            st.subheader("准入报告")
            intake_status = intake.get("status", "unknown")
            if intake_status == "passed":
                st.success("✅ 准入检查通过")
            elif intake_status == "blocked":
                st.warning(f"⚠️ 准入受阻: {intake.get('blocked_reason', '未知原因')}")
            with st.expander("查看原始准入报告"):
                st.json(intake)

        st.subheader("事件日志")
        events = get_events_tail(run_dir)
        if events:
            st.code("\n".join(events), language="json")
        else:
            st.caption("未找到事件记录。")

        if manifest is None and intake is None and gpu is None:
            st.info("尚无运行时数据。请先执行预检或真实 L3 管线。")

# ═══════════════════════════════════════════════════════════════════════════
# 页面 5: 最终审阅
# ═══════════════════════════════════════════════════════════════════════════
elif page == "5. 最终审阅":
    st.title("最终审阅")
    st.info("验收 L3 全链路结果：补丁是否真实、实验是否跑完、科学结论是否成立。")

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
            pipe = summary["pipeline_success"]
            noop = final_facts.get("noop_patch") if final_facts else None
            if pipe is True:
                st.success("✅ 补丁与管线\n管线全阶段通过\n真实补丁已应用")
            elif pipe is False:
                detail = "空补丁" if noop is True else "管线未全通过"
                st.error(f"❌ 补丁与管线\n{detail}")
            else:
                st.info("⏳ 补丁与管线\n数据尚未生成")

        with col2:
            exc = summary["execution_success"]
            if exc is True:
                st.success("✅ 执行完成度\nGPU 已验证\n全部实验单元完成")
            elif exc is False:
                st.error("❌ 执行完成度\n未验证或单元未完成")
            else:
                st.info("⏳ 执行完成度\n数据尚未生成")

        with col3:
            sci = summary["scientific_success"]
            claim = summary["scientific_claim"] or "—"
            if sci is True:
                st.success(f"✅ 科学结论\n已证明改进\n({claim})")
            elif sci is False:
                st.error(f"❌ 科学结论\n未证明改进\n({claim})")
            else:
                st.info(f"⏳ 科学结论\n数据尚未生成\n({claim})")

        st.markdown("---")

        # ── Plain-language explanation ─────────────────────────────────────
        if final_facts:
            st.subheader("结论解读")
            claim = final_facts.get("scientific_claim", "")
            noop = final_facts.get("noop_patch")
            mode = final_facts.get("execution_mode", "")
            device = final_facts.get("gpu_device_name", "—")

            if noop is True:
                st.warning("本次运行补丁为空，仅验证了管线连通性，不涉及科学改进。")
            elif claim == "mixed_or_inconclusive":
                st.info(
                    "管线运行正常，真实补丁已应用，GPU 已参与执行。"
                    "但本次实验**未观察到统计显著的科学改进**"
                    f"（结论：{claim}）。\n\n"
                    "这**不是管线失败**，而是科学上的保守结论 — "
                    "系统没有过度声称不存在的结果。"
                )
            elif claim == "not_established":
                st.warning("科学结论未能成立，可能原因：无有效配对观测、补丁未生效或管线未完成。")
            elif claim:
                st.success(f"科学结论：{claim}")

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
