"""Compatibility facade for the canonical V2 control-plane event store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.core.control_plane import ControlPlaneEventStore

EVENTS_DIR = "events"
EVENTS_FILE = "events.jsonl"
LOW_FREQUENCY_TYPED_EVENTS = {
    "planner.source_action.decided",
    "planner.turn_gate.decided",
    "planner.need_discovery.decided",
    "contract.draft.updated",
    "contract.confirmation.requested",
    "contract.confirmation.resolved",
    "prompt.trace.created",
    "schema.validation.failed",
}


def append_event(run_dir: Path, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    event = ControlPlaneEventStore(run_dir).append(event_type, payload)
    return event.model_dump(mode="json", exclude_none=True)


def append_event_once(
    run_dir: Path,
    event_type: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = ControlPlaneEventStore(run_dir).append_once(event_type, idempotency_key, payload)
    return event.model_dump(mode="json", exclude_none=True)


def append_typed_event(run_dir: Path, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if event_type not in LOW_FREQUENCY_TYPED_EVENTS:
        raise ValueError(f"unsupported low-frequency typed event: {event_type}")
    return append_event(run_dir, event_type, payload)


def load_events_since(run_dir: Path, last_event_id: int = 0) -> list[dict[str, Any]]:
    return [
        event.model_dump(mode="json", exclude_none=True)
        for event in ControlPlaneEventStore(run_dir).read_since(last_event_id)
    ]


def event_to_ws_message(evt: dict[str, Any]) -> dict[str, Any]:
    return {"type": evt["type"], **(evt.get("payload", {}) or {})}
