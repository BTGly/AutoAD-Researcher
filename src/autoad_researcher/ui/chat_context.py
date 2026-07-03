"""Construct LLM context from run artifacts for the Research Chat."""

from pathlib import Path
from typing import Any

from autoad_researcher.ui.artifact_viewer import (
    get_artifact_chain,
    get_events_tail,
    get_execution_manifest,
    get_final_facts,
    get_final_report_md,
    get_gpu_evidence,
    get_runner_intake_report,
    list_stage_dirs,
)

MAX_REPORT_CHARS = 5000
MAX_EVENTS_LINES = 20


def _truncate(text: str | None, max_chars: int) -> str | None:
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n…[截断: {len(text) - max_chars} 字省略]"


def build_chat_context(run_dir: Path) -> dict[str, Any]:
    """Build a compact context dict from run artifacts.

    Returns a dict suitable for serialisation into a system / user message.
    Missing artifacts are ``None`` rather than raising.
    """
    stages = list_stage_dirs(run_dir)
    existing = [s["name"] for s in stages if s["exists"]]

    final_md = _truncate(get_final_report_md(run_dir), MAX_REPORT_CHARS)
    events = get_events_tail(run_dir, n=MAX_EVENTS_LINES)

    return {
        "run_id": run_dir.name,
        "available_stages": existing,
        "execution_manifest": get_execution_manifest(run_dir),
        "runner_intake_report": get_runner_intake_report(run_dir),
        "gpu_evidence": get_gpu_evidence(run_dir),
        "final_facts": get_final_facts(run_dir),
        "artifact_chain": get_artifact_chain(run_dir),
        "events_tail": events if events else None,
        "final_report_excerpt": final_md,
    }
