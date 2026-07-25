"""Lightweight, read-only material context for research conversations."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from autoad_researcher.ui.sources import inspect_local_path, is_allowed_local_source_path

CONTEXT_DIR = "context"
CONTEXT_FILE = "session_context.json"


def _context_path(run_dir: Path) -> Path:
    return run_dir / CONTEXT_DIR / CONTEXT_FILE


def load_session_context(run_dir: Path) -> list[dict[str, Any]]:
    path = _context_path(run_dir)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    contexts = payload.get("contexts") if isinstance(payload, dict) else None
    return [item for item in contexts if isinstance(item, dict)] if isinstance(contexts, list) else []


def _save_session_context(run_dir: Path, contexts: list[dict[str, Any]]) -> None:
    path = _context_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"schema_version": 1, "contexts": contexts}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def extract_local_path_candidates(
    user_input: str,
    attachments: Iterable[str] | None = None,
) -> list[str]:
    """Extract explicit absolute path-shaped values without classifying them."""
    values: list[str] = []
    try:
        values.extend(shlex.split(user_input, posix=True))
    except ValueError:
        values.extend(user_input.split())
    values.extend(str(item) for item in (attachments or []) if isinstance(item, str))

    candidates: list[str] = []
    for value in values:
        candidate = _clean_path_candidate(value)
        if candidate is not None and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def attach_local_context(
    run_dir: Path,
    source_path: str | Path,
    *,
    user_label: str = "",
    user_hint: str = "",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Inspect and attach one local path as a read-only conversation context."""
    raw_path = str(source_path)
    try:
        path = Path(raw_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return _failure(raw_path, "LOCAL_PATH_INVALID", "无法解析本地路径"), None

    extra_roots = _session_workspace_roots(run_dir, path)
    if not is_allowed_local_source_path(path, additional_roots=extra_roots):
        return _failure(
            raw_path,
            "LOCAL_PATH_OUTSIDE_ALLOWED_ROOT",
            "路径不在当前 Run workspace 或已授权的本地资料目录内",
        ), None
    if not path.exists():
        return _failure(raw_path, "LOCAL_PATH_NOT_FOUND", "路径不存在"), None
    if not os.access(path, os.R_OK):
        return _failure(raw_path, "LOCAL_PATH_NOT_READABLE", "路径不可读"), None
    if not path.is_file() and not path.is_dir():
        return _failure(raw_path, "LOCAL_PATH_UNSUPPORTED", "路径不是文件或目录"), None

    try:
        inspection = inspect_local_path(path, additional_allowed_roots=extra_roots)
    except (OSError, ValueError) as exc:
        return _failure(raw_path, "LOCAL_PATH_INSPECTION_FAILED", str(exc)), None

    contexts = load_session_context(run_dir)
    canonical = str(path)
    existing = next((item for item in contexts if item.get("path") == canonical), None)
    context_id = str(existing.get("context_id")) if existing else _context_id(canonical)
    context = {
        "context_id": context_id,
        "path": canonical,
        "user_label": user_label.strip() or path.name or canonical,
        "user_hint": user_hint.strip(),
        "access": "read_only",
        "status": "readable",
        "inspection": inspection,
        "attached_at": (existing or {}).get("attached_at") or _now(),
    }
    if existing is not None:
        contexts = [context if item.get("path") == canonical else item for item in contexts]
        status = "context_already_attached"
    else:
        contexts.append(context)
        status = "context_attached"
    _save_session_context(run_dir, contexts)
    return {
        "kind": "session_context",
        "context_id": context_id,
        "source_path": raw_path,
        "path": canonical,
        "status": status,
        "access": "read_only",
        "inspection": inspection,
    }, context


def _session_workspace_roots(run_dir: Path, path: Path) -> list[Path]:
    """Allow current or sibling run workspaces below the configured runs root."""
    roots = [run_dir / "workspace"]
    configured_root = Path(os.environ.get("AUTOAD_RUNS_ROOT", "runs")).expanduser().resolve()
    for ancestor in (path, *path.parents):
        if ancestor.name != "workspace":
            continue
        try:
            ancestor.relative_to(configured_root)
        except ValueError:
            continue
        if ancestor not in roots:
            roots.append(ancestor)
    return roots


def _clean_path_candidate(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.startswith("file://"):
        parsed = urlsplit(candidate)
        candidate = unquote(parsed.path)
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    for delimiter in "，。；：、":
        candidate = candidate.split(delimiter, 1)[0]
    while candidate and candidate[-1] in "，。；：、,;!?)]}>`":
        candidate = candidate[:-1]
    return candidate or None


def _context_id(path: str) -> str:
    return "ctx_" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failure(path: str, code: str, message: str) -> dict[str, Any]:
    return {
        "kind": "session_context",
        "source_path": path,
        "status": "failed",
        "access": "read_only",
        "error": {"code": code, "message": message},
        "reason": message,
    }
