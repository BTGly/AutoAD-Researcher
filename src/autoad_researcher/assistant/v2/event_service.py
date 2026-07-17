"""Event log service for V2. JSONL-based event store.

Path: runs/{run_id}/events/events.jsonl
Worker writes events; WebSocket reads and pushes to clients.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENTS_DIR = "events"
EVENTS_FILE = "events.jsonl"


def _events_path(run_dir: Path) -> Path:
    return run_dir / EVENTS_DIR / EVENTS_FILE


def append_event(run_dir: Path, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    with _events_lock(run_dir):
        event = {
            "event_id": _next_event_id_unlocked(run_dir),
            "type": event_type,
            "payload": payload or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path = _events_path(run_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return event


def load_events_since(run_dir: Path, last_event_id: int = 0) -> list[dict[str, Any]]:
    path = _events_path(run_dir)
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                evt = json.loads(line)
                if evt.get("event_id", 0) > last_event_id:
                    events.append(evt)
            except json.JSONDecodeError:
                pass
    return events


def _next_event_id(run_dir: Path) -> int:
    return _next_event_id_unlocked(run_dir)


def _next_event_id_unlocked(run_dir: Path) -> int:
    path = _events_path(run_dir)
    if not path.is_file():
        return 1
    max_id = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                evt = json.loads(line)
                max_id = max(max_id, evt.get("event_id", 0))
            except json.JSONDecodeError:
                pass
    return max_id + 1


@contextmanager
def _events_lock(run_dir: Path, timeout: float = 5.0):
    lock_path = run_dir / EVENTS_DIR / ".events.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd = None
    while time.monotonic() < deadline:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            time.sleep(0.05)
    if fd is None:
        raise TimeoutError(f"Could not acquire events lock for {run_dir} within {timeout}s")
    try:
        yield
    finally:
        os.close(fd)
        try:
            os.unlink(lock_path)
        except OSError:
            pass
