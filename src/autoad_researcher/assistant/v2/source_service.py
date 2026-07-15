"""Unified source intake for V2 orchestrator.

Only structured source signals live here: uploads and explicit URLs. Natural
language tool intent remains part of the single research dialogue call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.source_normalizer import extract_first_source_candidate, extract_first_url, normalize_repository_reference


def classify_input(user_input: str, attachments: list[str] | None = None) -> str:
    """Classify only explicit source inputs into a source kind."""
    text = user_input.strip()

    if attachments:
        return "paper_pdf"

    candidate = extract_first_source_candidate(text)
    url = candidate.normalized_ref if candidate is not None else None
    if candidate is not None and candidate.source_kind == "github_repo":
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
        if source_kind == "github_repo":
            repo_candidate = normalize_repository_reference(source_url or user_input.strip())
            url = repo_candidate.normalized_ref if repo_candidate is not None else (source_url or user_input.strip())
        else:
            url = source_url or extract_first_url(user_input.strip()) or user_input.strip()
        result = register_url_source(run_dir, url, force_kind=source_kind)
        return {"source_id": result["source_id"], "kind": result["kind"], "status": result["status"]}

    sid = append_source_ref(
        run_dir,
        kind="paper_pdf",
        user_label=user_input[:50],
        stored_path=None,
        status="uploaded_not_parsed",
    )
    return {"source_id": sid, "kind": "paper_pdf", "status": "uploaded_not_parsed"}
