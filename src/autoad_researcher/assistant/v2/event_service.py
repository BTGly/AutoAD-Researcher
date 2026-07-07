"""Event log service for V2. JSONL-based event store.

Path: runs/{run_id}/events/events.jsonl
Worker writes events; WebSocket reads and pushes to clients.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENTS_DIR = "events"
EVENTS_FILE = "events.jsonl"


def _events_path(run_dir: Path) -> Path:
    return run_dir / EVENTS_DIR / EVENTS_FILE


def append_event(run_dir: Path, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    event = {
        "event_id": _next_event_id(run_dir),
        "type": event_type,
        "payload": payload or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _events_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
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
