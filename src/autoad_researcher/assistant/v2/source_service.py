"""Unified source intake for V2 orchestrator.

Maps user input to source kind and registers via existing sources.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def classify_input(user_input: str, attachments: list[str] | None = None) -> str:
    """Classify user input into a source kind."""
    text = user_input.strip()

    if attachments:
        return "paper_pdf"

    if "github.com" in text.lower():
        return "github_repo"

    if any(domain in text.lower() for domain in ("arxiv.org", "arxiv.org/abs", "arxiv.org/pdf")):
        return "webpage"

    if text.startswith("http://") or text.startswith("https://"):
        return "webpage"

    if any(kw in text for kw in ("搜索", "搜一下", "最新", "找论文", "找代码", "SOTA")):
        return "web_search"

    return "general_chat"


def register_source_intake(
    run_dir: Path,
    *,
    user_input: str,
    source_kind: str,
) -> dict[str, Any]:
    """Register a source and return its metadata."""
    from autoad_researcher.ui.sources import append_source_ref, register_url_source

    if source_kind in ("webpage", "github_repo"):
        result = register_url_source(run_dir, user_input.strip())
        return {"source_id": result["source_id"], "kind": result["kind"], "status": result["status"]}

    sid = append_source_ref(
        run_dir,
        kind="paper_pdf",
        user_label=user_input[:50],
        stored_path=None,
        status="uploaded_not_parsed",
    )
    return {"source_id": sid, "kind": "paper_pdf", "status": "uploaded_not_parsed"}
