"""Unified source intake for V2 orchestrator.

Maps user input to source kind and registers via existing sources.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _extract_clean_url(text: str) -> str | None:
    """Extract a clean URL from a line that may have user phrasing around it."""
    match = re.search(r"https?://[^\s\u4e00-\u9fff]+", text)
    return match.group(0).rstrip(".,;:!?)]}") if match else None


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
        url = _extract_clean_url(user_input.strip()) or user_input.strip()
        result = register_url_source(run_dir, url)
        return {"source_id": result["source_id"], "kind": result["kind"], "status": result["status"]}

    sid = append_source_ref(
        run_dir,
        kind="paper_pdf",
        user_label=user_input[:50],
        stored_path=None,
        status="uploaded_not_parsed",
    )
    return {"source_id": sid, "kind": "paper_pdf", "status": "uploaded_not_parsed"}
