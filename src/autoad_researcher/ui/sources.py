"""Source intake helpers — file upload + reference registry.

UI writes source_references.json when a user uploads a file or provides a
reference.  Status transitions are lightweight:
  uploaded_not_parsed → parsing → parsed / failed
  user_provided_not_ingested → (future: ingested)

This module does NOT call MinerU, download PDFs, clone repos, or run any
experiments.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SourceStatus = Literal[
    "uploaded_not_parsed",
    "user_provided_not_ingested",
    "parsing",
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
    "webpage",
    "user_text",
    "local_repo",
]

IntakeStatus = Literal[
    "pending",
    "running",
    "ok",
    "failed",
    "skipped",
]

ParseAttemptStatus = Literal[
    "running",
    "ok",
    "partial",
    "failed",
    "cancelled",
]

SOURCES_DIR = "sources"
REGISTRY_FILE = "source_references.json"
DEFAULT_LOCAL_SOURCE_ROOT = Path("/root/autodl-tmp/AI4S")
LOCAL_SOURCE_ROOTS_ENV = "AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS"
LEGACY_PARSE_ATTEMPT_ID = "legacy_active"


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
    registry = json.loads(path.read_text(encoding="utf-8"))
    return _registry_with_read_compat(run_dir, registry)


def _save_registry(run_dir: Path, registry: dict[str, Any]) -> None:
    path = _registry_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = _registry_for_disk(registry)
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def find_source_by_stored_path(run_dir: Path, stored_path: str) -> str | None:
    """Return the source_id matching *stored_path*, or None."""
    registry = load_source_registry(run_dir)
    for s in registry.get("sources", []):
        if s.get("stored_path") == stored_path:
            return s["source_id"]
    return None


def find_source_entry_by_stored_path(run_dir: Path, stored_path: str) -> dict[str, Any] | None:
    """Return the source registry entry matching *stored_path*, or None."""
    registry = load_source_registry(run_dir)
    for source in registry.get("sources", []):
        if source.get("stored_path") == stored_path:
            return source
    return None


def list_pdf_source_entries(run_dir: Path) -> list[dict[str, Any]]:
    """Return registered PDF sources with a stored run-relative path."""
    registry = load_source_registry(run_dir)
    entries: list[dict[str, Any]] = []
    for source in registry.get("sources", []):
        stored_path = source.get("stored_path")
        if source.get("kind") == "paper_pdf" and isinstance(stored_path, str) and stored_path:
            entries.append(source)
    return entries


def update_source_status(run_dir: Path, source_id: str, status: SourceStatus, *, error_message: str | None = None) -> None:
    registry = load_source_registry(run_dir)
    for s in registry["sources"]:
        if s.get("source_id") == source_id:
            s["status"] = status
            if error_message:
                s["error_message"] = error_message
            elif status != "failed":
                s.pop("error_message", None)
            break
    _save_registry(run_dir, registry)


def append_source_ref(
    run_dir: Path,
    *,
    kind: SourceKind,
    user_label: str,
    stored_path: str | None,
    status: SourceStatus,
    source_id: str | None = None,
    intake_status: IntakeStatus | None = None,
    intake_error: dict[str, Any] | None = None,
    active_parse_attempt_id: str | None = None,
    parse_attempts: list[dict[str, Any]] | None = None,
) -> str:
    sid = source_id or _generate_source_id()
    ref = {
        "source_id": sid,
        "kind": kind,
        "user_label": user_label,
        "status": status,
        "stored_path": stored_path,
        "created_at": _now_iso(),
        "intake_status": intake_status or _default_intake_status(status),
        "intake_error": intake_error,
        "active_parse_attempt_id": active_parse_attempt_id,
        "parse_attempts": list(parse_attempts or []),
    }
    registry = load_source_registry(run_dir)
    registry["sources"].append(ref)
    _save_registry(run_dir, registry)
    return sid


def append_source_parse_attempt(
    run_dir: Path,
    source_id: str,
    attempt: dict[str, Any],
    *,
    make_active: bool = False,
) -> None:
    """Append one parse attempt to a source registry entry.

    This helper only appends; it never replaces existing attempts.
    """
    registry = load_source_registry(run_dir)
    for source in registry["sources"]:
        if source.get("source_id") == source_id:
            attempts = [
                item
                for item in source.get("parse_attempts", [])
                if item.get("parse_attempt_id") != LEGACY_PARSE_ATTEMPT_ID
            ]
            attempts.append(dict(attempt))
            source["parse_attempts"] = attempts
            if make_active and attempt.get("status") == "ok":
                source["active_parse_attempt_id"] = attempt.get("parse_attempt_id")
            break
    _save_registry(run_dir, registry)


def update_source_parse_attempt(
    run_dir: Path,
    source_id: str,
    parse_attempt_id: str,
    updates: dict[str, Any],
    *,
    make_active: bool = False,
) -> None:
    """Update one existing parse attempt without replacing the attempt list."""
    registry = load_source_registry(run_dir)
    for source in registry["sources"]:
        if source.get("source_id") != source_id:
            continue
        for attempt in source.get("parse_attempts", []):
            if attempt.get("parse_attempt_id") == parse_attempt_id:
                attempt.update(updates)
                if make_active and _can_auto_activate_attempt(source, parse_attempt_id):
                    source["active_parse_attempt_id"] = parse_attempt_id
                _save_registry(run_dir, registry)
                return
        break
    raise KeyError(f"parse attempt not found: {source_id}/{parse_attempt_id}")


def set_active_parse_attempt(
    run_dir: Path,
    source_id: str,
    parse_attempt_id: str,
    *,
    reason: str = "user_switch",
) -> None:
    """Set the active parse attempt and record an audit event."""
    registry = load_source_registry(run_dir)
    old_active: str | None = None
    for source in registry["sources"]:
        if source.get("source_id") != source_id:
            continue
        attempts = source.get("parse_attempts", [])
        if not any(isinstance(item, dict) and item.get("parse_attempt_id") == parse_attempt_id for item in attempts):
            raise KeyError(f"parse attempt not found: {source_id}/{parse_attempt_id}")
        old_active = source.get("active_parse_attempt_id")
        source["active_parse_attempt_id"] = parse_attempt_id
        _save_registry(run_dir, registry)
        _record_active_parse_attempt_changed(
            run_dir,
            source_id=source_id,
            old_active_parse_attempt_id=old_active,
            new_active_parse_attempt_id=parse_attempt_id,
            reason=reason,
        )
        return
    raise KeyError(f"source not found: {source_id}")


def get_source_context(run_dir: Path) -> str:
    """Return a human-readable summary of the source registry for LLM context."""
    registry = load_source_registry(run_dir)
    sources = registry.get("sources", [])
    if not sources:
        return ""
    lines = ["SourceReferences（用户已提供但不一定已解析的资料）:"]
    for s in sources:
        sp = s.get("stored_path") or "—"
        lines.append(f"  - {s['source_id']}: {s['user_label']} ({s['status']}) path={sp}")
    return "\n".join(lines)


def _registry_with_read_compat(run_dir: Path, registry: dict[str, Any]) -> dict[str, Any]:
    sources = registry.get("sources", [])
    if not isinstance(sources, list):
        return {"schema_version": registry.get("schema_version", 1), "sources": []}
    normalized = dict(registry)
    normalized["sources"] = [_source_with_read_compat(run_dir, source) for source in sources if isinstance(source, dict)]
    return normalized


def _source_with_read_compat(run_dir: Path, source: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(source)
    status = normalized.get("status")
    normalized.setdefault("intake_status", _default_intake_status(status if isinstance(status, str) else None))
    normalized.setdefault("intake_error", None)

    attempts = normalized.get("parse_attempts")
    if isinstance(attempts, list):
        normalized["parse_attempts"] = [dict(item) for item in attempts if isinstance(item, dict)]
        normalized.setdefault("active_parse_attempt_id", None)
        return normalized

    if status == "parsed":
        normalized["parse_attempts"] = [_legacy_parse_attempt(run_dir)]
        normalized.setdefault("active_parse_attempt_id", LEGACY_PARSE_ATTEMPT_ID)
    else:
        normalized["parse_attempts"] = []
        normalized.setdefault("active_parse_attempt_id", None)
    return normalized


def _registry_for_disk(registry: dict[str, Any]) -> dict[str, Any]:
    payload = dict(registry)
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        payload["sources"] = []
        return payload
    payload["sources"] = [_source_for_disk(source) for source in sources if isinstance(source, dict)]
    return payload


def _source_for_disk(source: dict[str, Any]) -> dict[str, Any]:
    payload = dict(source)
    attempts = payload.get("parse_attempts")
    if isinstance(attempts, list):
        payload["parse_attempts"] = [
            dict(item)
            for item in attempts
            if isinstance(item, dict) and item.get("parse_attempt_id") != LEGACY_PARSE_ATTEMPT_ID
        ]
    if payload.get("active_parse_attempt_id") == LEGACY_PARSE_ATTEMPT_ID:
        payload["active_parse_attempt_id"] = None
    return payload


def _can_auto_activate_attempt(source: dict[str, Any], parse_attempt_id: str) -> bool:
    target = _find_parse_attempt(source, parse_attempt_id)
    if target is None:
        return False
    status = target.get("status")
    if status == "ok":
        return True
    if status == "partial":
        return not _active_attempt_is_ok(source)
    return False


def _active_attempt_is_ok(source: dict[str, Any]) -> bool:
    active_id = source.get("active_parse_attempt_id")
    if not isinstance(active_id, str):
        return False
    active = _find_parse_attempt(source, active_id)
    return bool(active and active.get("status") == "ok")


def _find_parse_attempt(source: dict[str, Any], parse_attempt_id: str) -> dict[str, Any] | None:
    attempts = source.get("parse_attempts", [])
    if not isinstance(attempts, list):
        return None
    for attempt in attempts:
        if isinstance(attempt, dict) and attempt.get("parse_attempt_id") == parse_attempt_id:
            return attempt
    return None


def _record_active_parse_attempt_changed(
    run_dir: Path,
    *,
    source_id: str,
    old_active_parse_attempt_id: str | None,
    new_active_parse_attempt_id: str,
    reason: str,
) -> None:
    from autoad_researcher.core.events import EventStore

    EventStore(runs_root=run_dir.parent).append(
        run_dir.name,
        "active_parse_attempt_changed",
        {
            "source_id": source_id,
            "old_active_parse_attempt_id": old_active_parse_attempt_id,
            "new_active_parse_attempt_id": new_active_parse_attempt_id,
            "reason": reason,
        },
    )


def _legacy_parse_attempt(run_dir: Path) -> dict[str, Any]:
    return {
        "parse_attempt_id": LEGACY_PARSE_ATTEMPT_ID,
        "parser": "unknown_legacy",
        "status": "ok",
        "output_dir": "paper/parse/",
        "quality_report": "paper/parse/parse_quality_report.json",
    }


def _default_intake_status(status: str | None) -> IntakeStatus:
    if status in {"uploaded_not_parsed", "parsing", "parsed"}:
        return "ok"
    if status == "failed":
        return "failed"
    if status == "user_provided_not_ingested":
        return "pending"
    return "pending"


# ── file upload ──


def _source_kind_for_name(name: str) -> SourceKind:
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        return "paper_pdf"
    if ext in (".md", ".markdown"):
        return "markdown"
    if ext == ".txt":
        return "text"
    raise ValueError("仅支持 PDF/txt/md/markdown")


def get_allowed_local_source_roots() -> list[Path]:
    """Return resolved roots allowed for server-local source intake."""
    raw = os.environ.get(LOCAL_SOURCE_ROOTS_ENV)
    if raw:
        roots = [Path(part).expanduser().resolve() for part in raw.split(":") if part.strip()]
        return roots or [DEFAULT_LOCAL_SOURCE_ROOT.resolve()]
    return [DEFAULT_LOCAL_SOURCE_ROOT.resolve()]


def _is_under_allowed_local_source_root(path: Path, allowed_roots: list[Path]) -> bool:
    for root in allowed_roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def save_uploaded_file(run_dir: Path, uploaded_file: Any) -> dict[str, Any]:
    """Save an uploaded file to runs/{run_id}/sources/ and record in registry.

    *uploaded_file* must have `.name` (str) and `.getvalue()` (→ bytes).
    Returns {"source_id", "stored_path", "kind"}.
    """
    name = Path(str(uploaded_file.name)).name
    if not name:
        raise ValueError("uploaded file name must not be empty")
    kind = _source_kind_for_name(name)

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


def register_local_file_source(run_dir: Path, source_path: str | Path) -> dict[str, Any]:
    """Copy an existing server-local source file into runs/{run_id}/sources/.

    This is for remote-server workflows where the PDF already exists on disk
    and browser upload is the wrong UX. It only registers supported local files;
    it does not parse, download, clone, or execute anything.
    """
    src = Path(source_path).expanduser().resolve()
    if not _is_under_allowed_local_source_root(src, get_allowed_local_source_roots()):
        raise ValueError("该路径不在允许的资料目录内")
    if not src.is_file():
        raise ValueError("该路径不是可注册的资料文件")

    name = src.name
    if not name:
        raise ValueError("local source file name must not be empty")
    kind = _source_kind_for_name(name)

    source_id = _generate_source_id()
    dest_dir = _resolve_sources_dir(run_dir) / source_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / name
    shutil.copyfile(src, dest_path)

    stored_path = dest_path.relative_to(run_dir).as_posix()
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
