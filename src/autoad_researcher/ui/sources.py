"""Source intake helpers — file upload + reference registry.

UI writes source_references.json when a user uploads a file or provides a
reference.  Status transitions are lightweight:
  uploaded_not_parsed → parsed / failed
  user_provided_not_ingested → (future: ingested)

This module does NOT call MinerU, download PDFs, clone repos, or run any
experiments.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SourceStatus = Literal[
    "uploaded_not_parsed",
    "user_provided_not_ingested",
    "parsed",
    "failed",
]

SourceKind = Literal[
    "paper_pdf",
    "text",
    "markdown",
    "github_repo",
    "arxiv_id",
    "doi",
    "url",
]

SOURCES_DIR = "sources"
REGISTRY_FILE = "source_references.json"


def _resolve_sources_dir(run_dir: Path) -> Path:
    return run_dir / SOURCES_DIR


def _registry_path(run_dir: Path) -> Path:
    return _resolve_sources_dir(run_dir) / REGISTRY_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_source_id() -> str:
    return f"src_{_now_iso().replace(':','-').replace('.','-')}"


# ── registry ──


def load_source_registry(run_dir: Path) -> dict[str, Any]:
    path = _registry_path(run_dir)
    if not path.is_file():
        return {"schema_version": 1, "sources": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_registry(run_dir: Path, registry: dict[str, Any]) -> None:
    path = _registry_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def find_source_by_stored_path(run_dir: Path, stored_path: str) -> str | None:
    """Return the source_id matching *stored_path*, or None."""
    registry = load_source_registry(run_dir)
    for s in registry.get("sources", []):
        if s.get("stored_path") == stored_path:
            return s["source_id"]
    return None


def update_source_status(run_dir: Path, source_id: str, status: SourceStatus, *, error_message: str | None = None) -> None:
    registry = load_source_registry(run_dir)
    for s in registry["sources"]:
        if s.get("source_id") == source_id:
            s["status"] = status
            if error_message:
                s["error_message"] = error_message
            break
    _save_registry(run_dir, registry)


def append_source_ref(run_dir: Path, *, kind: SourceKind, user_label: str, stored_path: str | None, status: SourceStatus, source_id: str | None = None) -> str:
    sid = source_id or _generate_source_id()
    ref = {
        "source_id": sid,
        "kind": kind,
        "user_label": user_label,
        "status": status,
        "stored_path": stored_path,
        "created_at": _now_iso(),
    }
    registry = load_source_registry(run_dir)
    registry["sources"].append(ref)
    _save_registry(run_dir, registry)
    return sid


def get_source_context(run_dir: Path) -> str:
    """Return a human-readable summary of the source registry for LLM context."""
    registry = load_source_registry(run_dir)
    sources = registry.get("sources", [])
    if not sources:
        return ""
    lines = ["SourceReferences（用户已提供但不一定已解析的资料）:"]
    for s in sources:
        lines.append(f"  - {s['source_id']}: {s['user_label']} ({s['status']})")
    return "\n".join(lines)


# ── file upload ──


def save_uploaded_file(run_dir: Path, uploaded_file: Any) -> dict[str, Any]:
    """Save an uploaded file to runs/{run_id}/sources/ and record in registry.

    *uploaded_file* must have `.name` (str) and `.getvalue()` (→ bytes).
    Returns {"source_id", "stored_path", "kind"}.
    """
    name = uploaded_file.name
    kind: SourceKind
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        kind = "paper_pdf"
    elif ext in (".md", ".markdown"):
        kind = "markdown"
    else:
        kind = "text"

    source_id = _generate_source_id()
    dest_dir = _resolve_sources_dir(run_dir) / source_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / name

    content = uploaded_file.getvalue()
    dest_path.write_bytes(content)

    stored_path = str(dest_path.relative_to(run_dir))
    append_source_ref(
        run_dir,
        kind=kind,
        user_label=name,
        stored_path=stored_path,
        status="uploaded_not_parsed",
        source_id=source_id,
    )
    return {
        "source_id": source_id,
        "stored_path": stored_path,
        "kind": kind,
    }


# ── path safety ──


def resolve_source_pdf_path_safely(run_dir: Path, user_text: str) -> Path | None:
    """Extract a PDF path from *user_text* and validate it lives under
    runs/{run_id}/sources/.  Returns None if nothing found or path is unsafe."""
    import re

    match = re.search(r"sources/((?:[^/\s]?[^/\s]+/)*[^/\s]+\.pdf)", user_text, re.IGNORECASE)
    if not match:
        return None
    relative = match.group(1)
    candidate = run_dir / "sources" / relative
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    allowed = (_resolve_sources_dir(run_dir)).resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError:
        return None
    if not resolved.is_file():
        return None
    return resolved
