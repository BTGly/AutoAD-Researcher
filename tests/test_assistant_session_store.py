"""Tests for Assistant SessionStore persistence."""

import json

import pytest

from autoad_researcher.assistant.events import AssistantEvent
from autoad_researcher.assistant.session import AutoADAssistantSession
from autoad_researcher.assistant.session_store import AssistantTransitionRecord, SessionStore


def test_save_and_load_session_roundtrip(tmp_path):
    store = SessionStore(runs_root=tmp_path)
    session = AutoADAssistantSession(session_id="s1", run_id="run_001", mode="intent_structuring")

    path = store.save_session(session)
    loaded = store.require_session("run_001")

    assert path == tmp_path / "run_001" / "assistant" / "session.json"
    assert loaded == session


def test_load_missing_session_returns_none(tmp_path):
    store = SessionStore(runs_root=tmp_path)

    assert store.load_session("run_missing") is None


def test_require_missing_session_raises(tmp_path):
    store = SessionStore(runs_root=tmp_path)

    with pytest.raises(FileNotFoundError, match="assistant session not found"):
        store.require_session("run_missing")


def test_append_and_read_events_jsonl(tmp_path):
    store = SessionStore(runs_root=tmp_path)
    event = AssistantEvent(
        event_id="ev_001",
        event_type="user_input",
        payload={"text": "继续这个异常检测方向"},
        router_labels=["goal_update"],
    )

    path = store.append_event("run_001", event)
    events = store.read_events("run_001")

    assert path == tmp_path / "run_001" / "assistant" / "events.jsonl"
    assert events == [event]


def test_append_and_read_transition_jsonl(tmp_path):
    store = SessionStore(runs_root=tmp_path)
    record = AssistantTransitionRecord(
        run_id="run_001",
        event_id="ev_001",
        from_mode="goal_alignment",
        to_mode="intent_structuring",
        reason="artifact context available",
    )

    path = store.append_transition(record)
    transitions = store.read_transitions("run_001")

    assert path == tmp_path / "run_001" / "assistant" / "transitions.jsonl"
    assert transitions[0].from_mode == "goal_alignment"
    assert transitions[0].to_mode == "intent_structuring"
    assert transitions[0].reason == "artifact context available"


def test_empty_jsonl_reads_return_empty_lists(tmp_path):
    store = SessionStore(runs_root=tmp_path)

    assert store.read_events("run_001") == []
    assert store.read_transitions("run_001") == []


def test_store_rejects_unsafe_run_id(tmp_path):
    store = SessionStore(runs_root=tmp_path)

    with pytest.raises(ValueError, match="run_id"):
        store.session_path("../escape")


def test_invalid_jsonl_record_is_rejected(tmp_path):
    store = SessionStore(runs_root=tmp_path)
    path = store.events_path("run_001")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(["not", "an", "object"]) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="jsonl record must be object"):
        store.read_events("run_001")
