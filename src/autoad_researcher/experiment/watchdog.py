"""Deterministic runtime health checks for a running ExperimentAttempt."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

_NON_FINITE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:nan|[+-]?inf(?:inity)?)(?![A-Za-z0-9_])", re.IGNORECASE
)


class AttemptHealthEvent(BaseModel):
    """Append-only evidence produced while the process is still running."""

    model_config = ConfigDict(extra="forbid")

    event: str
    timestamp: str
    stderr_snippet: str | None = None


class RuntimeWatchdog:
    """Check process liveness and known deterministic failure signatures."""

    def __init__(self, *, heartbeat_interval_seconds: int = 15, stdout_stall_seconds: int = 300):
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.stdout_stall_seconds = stdout_stall_seconds

    def inspect(self, attempt_dir: Path, *, pid: int | None, now: datetime | None = None) -> list[AttemptHealthEvent]:
        current = now or datetime.now(timezone.utc)
        events: list[AttemptHealthEvent] = []
        stderr = _tail(attempt_dir / "stderr.log")
        lower = stderr.lower()
        if "cuda out of memory" in lower:
            events.append(_event("OOM_DETECTED", current, stderr))
        if _NON_FINITE_PATTERN.search(stderr):
            events.append(_event("NAN_OR_INF", current, stderr))
        heartbeat = _read_timestamp(attempt_dir / "heartbeat.json")
        if heartbeat is not None and (current - heartbeat).total_seconds() > self.heartbeat_interval_seconds * 2:
            events.append(_event("STALE_HEARTBEAT", current))
        stdout = attempt_dir / "stdout.log"
        if stdout.is_file() and (current.timestamp() - stdout.stat().st_mtime) > self.stdout_stall_seconds:
            events.append(_event("STDOUT_STALLED", current))
        if pid is not None and not _pid_alive(pid):
            events.append(_event("PROCESS_DEAD", current))
        return _append_new_events(attempt_dir / "health_events.jsonl", events)


def _append_new_events(path: Path, events: list[AttemptHealthEvent]) -> list[AttemptHealthEvent]:
    existing = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line).get("event"))
            except json.JSONDecodeError:
                continue
    new = [event for event in events if event.event not in existing]
    if new:
        with path.open("a", encoding="utf-8") as handle:
            for event in new:
                handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
    return new


def _event(name: str, current: datetime, stderr: str | None = None) -> AttemptHealthEvent:
    return AttemptHealthEvent(event=name, timestamp=current.isoformat(), stderr_snippet=stderr[:500] if stderr else None)


def _tail(path: Path, limit: int = 4000) -> str:
    if not path.is_file(): return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


def _read_timestamp(path: Path) -> datetime | None:
    if not path.is_file(): return None
    try: value = json.loads(path.read_text(encoding="utf-8")).get("timestamp")
    except json.JSONDecodeError: return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError): return None


def _pid_alive(pid: int) -> bool:
    try: os.kill(pid, 0)
    except OSError: return False
    return True
