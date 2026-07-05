"""SessionStore for AutoAD Assistant control artifacts.

The store owns only assistant control artifacts under runs/{run_id}/assistant/.
It does not call LLMs, does not execute the pipeline, and does not accept user
input as path components beyond the validated run_id.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.events import AssistantEvent
from autoad_researcher.assistant.session import AssistantMode, AutoADAssistantSession
from autoad_researcher.core.run_id import run_dir_path


ASSISTANT_DIR = Path("assistant")
SESSION_ARTIFACT = ASSISTANT_DIR / "session.json"
EVENTS_ARTIFACT = ASSISTANT_DIR / "events.jsonl"
TRANSITIONS_ARTIFACT = ASSISTANT_DIR / "transitions.jsonl"


class AssistantTransitionRecord(BaseModel):
    """One persisted assistant mode transition."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    from_mode: AssistantMode
    to_mode: AssistantMode
    reason: str | None = None
    violations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SessionStore:
    """Persist minimal assistant session state and append-only audit streams."""

    def __init__(self, runs_root: str | Path = "runs") -> None:
        self._runs_root = Path(runs_root)

    def run_dir(self, run_id: str) -> Path:
        return run_dir_path(self._runs_root, run_id)

    def assistant_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / ASSISTANT_DIR

    def session_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / SESSION_ARTIFACT

    def events_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / EVENTS_ARTIFACT

    def transitions_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / TRANSITIONS_ARTIFACT

    def save_session(self, session: AutoADAssistantSession) -> Path:
        path = self.session_path(session.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(session.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_session(self, run_id: str) -> AutoADAssistantSession | None:
        path = self.session_path(run_id)
        if not path.exists():
            return None
        return AutoADAssistantSession.model_validate_json(path.read_text(encoding="utf-8"))

    def require_session(self, run_id: str) -> AutoADAssistantSession:
        session = self.load_session(run_id)
        if session is None:
            raise FileNotFoundError(f"assistant session not found for run_id: {run_id}")
        return session

    def append_event(self, run_id: str, event: AssistantEvent) -> Path:
        return self._append_jsonl(self.events_path(run_id), event.model_dump(mode="json"))

    def read_events(self, run_id: str) -> list[AssistantEvent]:
        return [AssistantEvent.model_validate(item) for item in self._read_jsonl(self.events_path(run_id))]

    def append_transition(self, record: AssistantTransitionRecord) -> Path:
        return self._append_jsonl(self.transitions_path(record.run_id), record.model_dump(mode="json"))

    def read_transitions(self, run_id: str) -> list[AssistantTransitionRecord]:
        return [
            AssistantTransitionRecord.model_validate(item)
            for item in self._read_jsonl(self.transitions_path(run_id))
        ]

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, object]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=False))
            handle.write("\n")
        return path

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, object]]:
        if not path.exists():
            return []
        records: list[dict[str, object]] = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            data = json.loads(line)
            if not isinstance(data, dict):
                raise ValueError(f"assistant jsonl record must be object: {path}:{lineno}")
            records.append(data)
        return records
