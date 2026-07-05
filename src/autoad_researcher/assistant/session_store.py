"""SessionStore — persist and restore Assistant session, events, transitions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.events import AssistantEvent
from autoad_researcher.assistant.session import AutoADAssistantSession
from autoad_researcher.core.run_id import run_dir_path, validate_run_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _assistant_dir(runs_root: str | Path, run_id: str) -> Path:
    validate_run_id(runs_root, run_id)
    p = run_dir_path(runs_root, run_id) / "assistant"
    p.mkdir(parents=True, exist_ok=True)
    return p


class AssistantTransitionRecord(BaseModel):
    """One transition record for audit trail."""
    model_config = ConfigDict(extra="forbid")
    run_id: str
    event_id: str
    from_mode: str
    to_mode: str
    triggered_by: str = ""
    reason: str = ""
    recorded_at: str = Field(default_factory=_now_iso)


class SessionStore:
    """Persist and restore Assistant session, events, and transitions."""

    def __init__(self, runs_root: str | Path = "runs") -> None:
        self.runs_root = runs_root

    # ── session ──

    def load_session(self, run_id: str) -> AutoADAssistantSession | None:
        path = self.session_path(run_id)
        if not path.is_file():
            return None
        return AutoADAssistantSession.model_validate_json(path.read_text(encoding="utf-8"))

    def save_session(self, session: AutoADAssistantSession) -> Path:
        path = self.session_path(session.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(session.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def require_session(self, run_id: str) -> AutoADAssistantSession:
        session = self.load_session(run_id)
        if session is None:
            raise FileNotFoundError(f"assistant session not found: run_id={run_id}")
        return session

    # ── events ──

    def append_event(self, run_id: str, event: AssistantEvent) -> Path:
        path = self.events_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = event.model_dump()
        record["_recorded_at"] = _now_iso()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return path

    def read_events(self, run_id: str) -> list[AssistantEvent]:
        path = self.events_path(run_id)
        if not path.is_file():
            return []
        events: list[AssistantEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError("jsonl record must be object, not array")
            obj.pop("_recorded_at", None)
            events.append(AssistantEvent.model_validate(obj))
        return events

    # ── transitions ──

    def append_transition(self, record: AssistantTransitionRecord) -> Path:
        path = _assistant_dir(self.runs_root, record.run_id) / "transitions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = record.model_dump()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        return path

    def read_transitions(self, run_id: str) -> list[AssistantTransitionRecord]:
        path = _assistant_dir(self.runs_root, run_id) / "transitions.jsonl"
        if not path.is_file():
            return []
        records: list[AssistantTransitionRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(AssistantTransitionRecord.model_validate_json(line))
        return records

    # ── paths ──

    def session_path(self, run_id: str) -> Path:
        validate_run_id(self.runs_root, run_id)
        return run_dir_path(self.runs_root, run_id) / "assistant" / "session.json"

    def events_path(self, run_id: str) -> Path:
        validate_run_id(self.runs_root, run_id)
        return run_dir_path(self.runs_root, run_id) / "assistant" / "events.jsonl"


# ── standalone convenience wrappers ──


def load_session(runs_root: str | Path, run_id: str) -> AutoADAssistantSession | None:
    return SessionStore(runs_root).load_session(run_id)


def save_session(runs_root: str | Path, session: AutoADAssistantSession) -> Path:
    return SessionStore(runs_root).save_session(session)


def append_event(runs_root: str | Path, run_id: str, event: AssistantEvent) -> Path:
    return SessionStore(runs_root).append_event(run_id, event)


def read_events(runs_root: str | Path, run_id: str) -> list[AssistantEvent]:
    return SessionStore(runs_root).read_events(run_id)


def append_transition_kw(
    runs_root: str | Path,
    run_id: str,
    *,
    event_id: str,
    from_mode: str,
    to_mode: str,
    triggered_by: str,
    reason: str = "",
) -> Path:
    return SessionStore(runs_root).append_transition(
        AssistantTransitionRecord(
            run_id=run_id,
            event_id=event_id,
            from_mode=from_mode,
            to_mode=to_mode,
            triggered_by=triggered_by,
            reason=reason,
        )
    )


def append_transition(
    runs_root: str | Path,
    run_id: str,
    *,
    event_id: str,
    from_mode: str,
    to_mode: str,
    triggered_by: str,
    reason: str = "",
) -> Path:
    return append_transition_kw(
        runs_root, run_id,
        event_id=event_id, from_mode=from_mode, to_mode=to_mode,
        triggered_by=triggered_by, reason=reason,
    )
