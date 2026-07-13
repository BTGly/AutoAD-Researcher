"""Strict, durable V2 event projection store."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from autoad_researcher.core.control_plane.errors import (
    CorruptAuditProjection,
    EventIdempotencyConflict,
)
from autoad_researcher.core.control_plane.hashing import event_payload_sha256
from autoad_researcher.core.control_plane.io import append_jsonl_line_durable
from autoad_researcher.core.control_plane.lock import AdvisoryFileLock
from autoad_researcher.core.control_plane.models import ControlPlaneEvent


class ControlPlaneEventStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "events" / "events.jsonl"
        self.lock_path = self.run_dir / "events" / ".events.lock"

    def read_since(self, last_event_id: int = 0) -> list[ControlPlaneEvent]:
        with AdvisoryFileLock(self.lock_path, mode="shared"):
            return [event for event in self._load_strict() if event.event_id > last_event_id]

    def append(self, event_type: str, payload: dict[str, Any] | None = None) -> ControlPlaneEvent:
        return self._append_locked(event_type=event_type, payload=payload or {}, idempotency_key=None)

    def append_once(
        self,
        event_type: str,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
    ) -> ControlPlaneEvent:
        if not idempotency_key:
            raise ValueError("event idempotency_key must not be empty")
        return self._append_locked(
            event_type=event_type,
            payload=payload or {},
            idempotency_key=idempotency_key,
        )

    def _append_locked(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None,
    ) -> ControlPlaneEvent:
        with AdvisoryFileLock(self.lock_path, mode="exclusive"):
            events = self._load_strict()
            payload_hash = event_payload_sha256(event_type=event_type, payload=payload)
            if idempotency_key is not None:
                for existing in events:
                    if existing.idempotency_key != idempotency_key:
                        continue
                    if existing.type == event_type and existing.payload_sha256 == payload_hash:
                        return existing
                    raise EventIdempotencyConflict(
                        f"event key {idempotency_key!r} reused with different content"
                    )
            event = ControlPlaneEvent(
                event_id=max((item.event_id for item in events), default=0) + 1,
                type=event_type,
                payload=payload,
                created_at=datetime.now(timezone.utc),
                idempotency_key=idempotency_key,
                payload_sha256=payload_hash if idempotency_key is not None else None,
            )
            append_jsonl_line_durable(self.path, event.model_dump(mode="json", exclude_none=True))
            return event

    def _load_strict(self) -> list[ControlPlaneEvent]:
        if not self.path.is_file():
            return []
        events: list[ControlPlaneEvent] = []
        seen_ids: set[int] = set()
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                event = ControlPlaneEvent.model_validate(raw)
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                raise CorruptAuditProjection(f"invalid event at {self.path}:{line_no}") from exc
            if event.event_id in seen_ids:
                raise CorruptAuditProjection(f"duplicate event_id={event.event_id} at {self.path}:{line_no}")
            seen_ids.add(event.event_id)
            events.append(event)
        return events
