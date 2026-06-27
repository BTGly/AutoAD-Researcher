"""Streamlit UI — Phase 1: Artifact viewer + preflight runner."""

import os
import sys

import streamlit as st

# Ensure project root is on path
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
    page_title="AutoAD Researcher — L3 Dashboard",
    page_icon="🔬",
    layout="wide",
)

# ── Session state defaults ────────────────────────────────────────────────
_DEFAULTS = {
    "run_id": "run_l3_bottle_001",
    "dataset_root": "/root/autodl-tmp/mvtec",
    "provider_base_url": "https://api.deepseek.com",
    "mode": "l3-preflight",
    "api_key": "",
    "preflight_result": None,
    "preflight_running": False,
}
for k, v in _DEFAULTS.items():
    st.session_state.setdefault(k, v)

# ── Sidebar navigation ────────────────────────────────────────────────────
PAGES = [
    "1. Run Config",
    "2. Preflight Runner",
    "3. Artifact Explorer",
    "4. Execution Monitor",
    "5. Final Review",
]
page = st.sidebar.radio("Navigate", PAGES, index=0)

st.sidebar.markdown("---")
st.sidebar.caption(f"Run: `{st.session_state.run_id}`")

# ═══════════════════════════════════════════════════════════════════════════
# PAGE 1: Run Config
# ═══════════════════════════════════════════════════════════════════════════
if page == "1. Run Config":
    st.title("Run Configuration")
    st.caption("Set parameters for a new L3 run. Changes take effect immediately.")

    col1, col2 = st.columns(2)
    with col1:
        st.text_input("Run ID", key="run_id")
        st.text_input("Dataset root", key="dataset_root")
    with col2:
        st.text_input("Provider base URL", key="provider_base_url")
        st.selectbox("Mode", ["l3-preflight"], key="mode", disabled=True,
                     help="Preflight only from UI. Real L3: see command below.")

    st.text_input("DeepSeek API key", type="password", key="api_key")
    if st.session_state.api_key:
        st.caption("API key is held in memory only — never displayed, logged, or written to disk.")

    st.markdown("---")
    st.subheader("Equivalent command")
    cmd = (
        f"uv run autoad stage3-acceptance "
        f"--run-id {st.session_state.run_id} "
        f"--mode {st.session_state.mode} "
        f"--provider-base-url {st.session_state.provider_base_url} "
        f"--json"
    )
    st.code(cmd, language="bash")
    st.caption("Copy this to run manually, or use the Preflight Runner tab.")

# ═══════════════════════════════════════════════════════════════════════════
# PAGE 2: Preflight Runner
# ═══════════════════════════════════════════════════════════════════════════
elif page == "2. Preflight Runner":
    st.title("Preflight Runner")
    st.caption("Executes `stage3-acceptance --mode l3-preflight` (no API calls, no real execution).")

    if not st.session_state.api_key:
        st.warning("Set an API key in Run Config first.")

    col1, col2 = st.columns([1, 3])
    with col1:
        run_btn = st.button(
            "Run Preflight",
            disabled=not st.session_state.api_key or st.session_state.preflight_running,
            type="primary",
        )
    with col2:
        if st.session_state.preflight_running:
            st.info("Running preflight... (up to 60 s)")

    if run_btn:
        st.session_state.preflight_running = True
        st.session_state.preflight_result = None
        with st.spinner("Running preflight..."):
            result = run_preflight(
                run_id=st.session_state.run_id,
                provider_base_url=st.session_state.provider_base_url,
                api_key=st.session_state.api_key,
                dataset_root=st.session_state.dataset_root,
            )
        st.session_state.preflight_result = result
        st.session_state.preflight_running = False
        st.rerun()

    if st.session_state.preflight_result:
        result = st.session_state.preflight_result
        status = result.get("status", "unknown")
        if status == "subprocess_failed":
            st.error(f"Subprocess failed (rc={result.get('returncode')})")
            if result.get("stderr"):
                st.code(result["stderr"], language="text")
            if result.get("stdout"):
                st.code(result["stdout"], language="json")
        elif status == "timeout":
            st.error("Preflight timed out after 300 s.")
        elif status == "error":
            st.error(f"Error: {result.get('error')}")
        else:
            st.success("Preflight completed.")
            st.json(result)

    if not st.session_state.preflight_result and not run_btn:
        st.info("Click **Run Preflight** to start.")

    st.markdown("---")
    st.subheader("Real L3 Execution")
    st.warning("This UI does NOT trigger real L3 execution. Run manually:")
    real_cmd = (
        f"AUTOAD_L3_REAL_EXECUTION_ALLOWED=1 \\\n"
        f"uv run autoad stage3-acceptance "
        f"--run-id {st.session_state.run_id} "
        f"--mode {st.session_state.mode} "
        f"--provider-base-url {st.session_state.provider_base_url} "
        f"--json"
    )
    st.code(real_cmd, language="bash")

# ═══════════════════════════════════════════════════════════════════════════
# PAGE 3: Artifact Explorer
# ═══════════════════════════════════════════════════════════════════════════
elif page == "3. Artifact Explorer":
    st.title("Artifact Explorer")
    try:
        run_dir = run_dir_path("runs", st.session_state.run_id)
    except ValueError as exc:
        st.error(f"Invalid run_id: {exc}")
        run_dir = None

    if run_dir is None or not run_dir.is_dir():
        st.warning(f"Run directory not found: `{run_dir}`")
    else:
        stages = list_stage_dirs(run_dir)
        for s in stages:
            with st.expander(f"{'✅' if s['exists'] else '⏳'} **{s['name']}**", expanded=s['exists']):
                if not s['exists']:
                    st.caption("Not found / not generated yet.")
                else:
                    files = list_artifact_files(run_dir, s['name'])
                    if files:
                        rows = [{"name": f["name"], "size": f"{f['size']:,} B", "path": f["path"]} for f in files]
                        st.dataframe(rows, use_container_width=True)
                    else:
                        st.caption("Empty directory.")

# ═══════════════════════════════════════════════════════════════════════════
# PAGE 4: Execution Monitor
# ═══════════════════════════════════════════════════════════════════════════
elif page == "4. Execution Monitor":
    st.title("Execution Monitor")
    try:
        run_dir = run_dir_path("runs", st.session_state.run_id)
    except ValueError as exc:
        st.error(f"Invalid run_id: {exc}")
        run_dir = None

    if run_dir is None or not run_dir.is_dir():
        st.warning(f"Run directory not found: `{run_dir}`")
    else:
        col_refresh, _ = st.columns([1, 5])
        with col_refresh:
            st.button("Refresh", key="_refresh_monitor")

        manifest = get_execution_manifest(run_dir)
        intake = get_runner_intake_report(run_dir)
        gpu = get_gpu_evidence(run_dir)

        tabs = st.tabs(["Execution Manifest", "Intake Report", "GPU Evidence", "Events"])
        with tabs[0]:
            if manifest:
                st.json(manifest)
            else:
                st.caption("Not found / not generated yet.")
        with tabs[1]:
            if intake:
                st.json(intake)
            else:
                st.caption("Not found / not generated yet.")
        with tabs[2]:
            if gpu:
                st.json(gpu)
            else:
                st.caption("Not found / not generated yet.")
        with tabs[3]:
            events = get_events_tail(run_dir)
            if events:
                st.code("\n".join(events), language="json")
            else:
                st.caption("No events found.")

# ═══════════════════════════════════════════════════════════════════════════
# PAGE 5: Final Review
# ═══════════════════════════════════════════════════════════════════════════
elif page == "5. Final Review":
    st.title("Final Review")
    try:
        run_dir = run_dir_path("runs", st.session_state.run_id)
    except ValueError as exc:
        st.error(f"Invalid run_id: {exc}")
        run_dir = None

    if run_dir is None or not run_dir.is_dir():
        st.warning(f"Run directory not found: `{run_dir}`")
    else:
        col_refresh, _ = st.columns([1, 5])
        with col_refresh:
            st.button("Refresh", key="_refresh_review")

        final_facts = get_final_facts(run_dir)
        manifest = get_execution_manifest(run_dir)
        summary = summarize_final_status(final_facts, manifest)

        # ── Three-panel status ────────────────────────────────────────────
        col1, col2, col3 = st.columns(3)

        with col1:
            eng = summary["engineering_success"]
            if eng is True:
                st.success("✅ Engineering\nPipeline completed,\nreal patch applied")
            elif eng is False:
                st.error("❌ Engineering\nNoop patch or\npipeline incomplete")
            else:
                st.info("⏳ Engineering\nNot available yet")

        with col2:
            exc = summary["execution_success"]
            if exc is True:
                st.success("✅ GPU Execution\nGPU verified,\n3/0/0 units")
            elif exc is False:
                st.error("❌ GPU Execution\nNot verified or\nunits failed")
            else:
                st.info("⏳ GPU Execution\nNot available yet")

        with col3:
            sci = summary["scientific_success"]
            claim = summary["scientific_claim"] or "—"
            if sci is True:
                st.success(f"✅ Scientific\nImprovement demonstrated\n({claim})")
            elif sci is False:
                st.error(f"❌ Scientific\nNot demonstrated\n({claim})")
            else:
                st.info(f"⏳ Scientific\nNot available yet\n({claim})")

        # ── Artifact chain ────────────────────────────────────────────────
        st.markdown("---")
        st.subheader("Artifact Chain")
        chain = get_artifact_chain(run_dir)
        rows = []
        for c in chain:
            icon = "✅" if c["handoff_sha"] != "—" and c["exists"] else "⏳"
            rows.append({"stage": icon + " " + c["stage"], "handoff SHA": c["handoff_sha"]})
        st.dataframe(rows, use_container_width=True)

        # ── Final facts JSON ──────────────────────────────────────────────
        st.markdown("---")
        st.subheader("Final Report Facts")
        if final_facts:
            st.json(final_facts)
        else:
            st.caption("Not found / not generated yet.")

        # ── Final report markdown ─────────────────────────────────────────
        st.subheader("Final Report (Markdown)")
        md = get_final_report_md(run_dir)
        if md:
            st.markdown(md)
        else:
            st.caption("Not found / not generated yet.")
