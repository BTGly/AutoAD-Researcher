"""Unified source intake for V2 orchestrator.

Only structured source signals live here: uploads and explicit URLs. Natural
language tool intent is planned by ``source_action_planner``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import re


def _extract_clean_url(text: str) -> str | None:
    """Extract a clean URL from a line that may have user phrasing around it."""
    match = re.search(r"https?://[^\s\u4e00-\u9fff]+", text)
    return match.group(0).rstrip(".,;:!?)]}") if match else None


def classify_input(user_input: str, attachments: list[str] | None = None) -> str:
    """Classify only explicit source inputs into a source kind."""
    text = user_input.strip()

    if attachments:
        return "paper_pdf"

    url = _extract_clean_url(text)
    if url and _is_github_url(url):
        return "github_repo"

    if url:
        return "webpage"

    if text.startswith("http://") or text.startswith("https://"):
        return "webpage"

    return "general_chat"


def register_source_intake(
    run_dir: Path,
    *,
    user_input: str,
    source_kind: str,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Register a source and return its metadata."""
    from autoad_researcher.ui.sources import append_source_ref, register_url_source

    if source_kind in ("webpage", "github_repo"):
        url = source_url or _extract_clean_url(user_input.strip()) or user_input.strip()
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


def _is_github_url(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    return hostname == "github.com" or hostname.endswith(".github.com")
