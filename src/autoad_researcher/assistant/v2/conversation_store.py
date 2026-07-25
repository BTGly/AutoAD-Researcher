"""Durable V2 conversation messages shared by chat and background workers."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

TRANSCRIPT_RELATIVE_PATH = Path("chat") / "transcript.jsonl"


def append_message(
    run_dir: Path,
    *,
    role: str,
    content: str,
    message_kind: str = "chat_reply",
    message_id: str | None = None,
    notification_key: str | None = None,
    job_id: str | None = None,
    source_id: str | None = None,
    artifact_paths: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    error: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Append one message, or return its existing notification by key."""

    if role not in {"user", "assistant"}:
        raise ValueError("conversation role must be user or assistant")
    if not content.strip():
        raise ValueError("conversation content must not be empty")
    with _transcript_lock(run_dir):
        entries = _load_unlocked(run_dir)
        if notification_key:
            existing = next(
                (item for item in entries if item.get("notification_key") == notification_key),
                None,
            )
            if existing is not None:
                return existing, False
        message = {
            "message_id": message_id or f"msg_{uuid4().hex}",
            "role": role,
            "content": content,
            "message_kind": message_kind,
            "notification_key": notification_key,
            "job_id": job_id,
            "source_id": source_id,
            "artifact_paths": list(artifact_paths or []),
            "evidence_ids": list(evidence_ids or []),
            "error": error,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path = run_dir / TRANSCRIPT_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return message, True


def load_messages(run_dir: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    with _transcript_lock(run_dir):
        entries = _load_unlocked(run_dir)
    return entries[-limit:] if limit is not None else entries


def load_message_tail_for_llm(run_dir: Path, *, limit: int = 12) -> list[dict[str, str]]:
    return [
        {"role": str(item["role"]), "content": str(item["content"])}
        for item in load_messages(run_dir, limit=limit)
        if item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
    ]


def _load_unlocked(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / TRANSCRIPT_RELATIVE_PATH
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("role") in {"user", "assistant"} and isinstance(value.get("content"), str):
            entries.append(value)
    return entries


@contextmanager
def _transcript_lock(run_dir: Path, *, timeout: float = 5.0):
    path = run_dir / "chat" / ".transcript.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd: int | None = None
    while time.monotonic() < deadline:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            time.sleep(0.01)
    if fd is None:
        raise TimeoutError("conversation transcript lock timeout")
    try:
        yield
    finally:
        os.close(fd)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
