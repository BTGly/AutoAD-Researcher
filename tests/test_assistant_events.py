"""Tests for AssistantEvent envelope."""

import pytest
from pydantic import ValidationError

from autoad_researcher.assistant.events import AssistantEvent


def _event(event_type="user_input", **overrides):
    kwargs = {
        "event_id": "ev_001",
        "event_type": event_type,
    }
    kwargs.update(overrides)
    return AssistantEvent(**kwargs)


class TestEventCreation:
    def test_user_input_event(self):
        e = _event(
            payload={"text": "我想降低 PatchCore 显存"},
            router_labels=["goal_update"],
            confidence=0.85,
        )
        assert e.event_type == "user_input"
        assert e.confidence == 0.85
        assert "goal_update" in e.router_labels

    def test_source_input_event(self):
        e = _event(
            event_type="source_input",
            payload={"source_paths": ["paper.pdf"]},
        )
        assert e.event_type == "source_input"

    def test_unknown_event(self):
        e = _event(event_type="unknown", confidence=0.3)
        assert e.event_type == "unknown"

    def test_task_decision_event(self):
        e = _event(
            event_type="task_decision",
            router_labels=["confirmation"],
        )
        assert e.event_type == "task_decision"


class TestEventConstraints:
    def test_requires_event_id(self):
        with pytest.raises(ValidationError):
            AssistantEvent(event_id="", event_type="user_input")

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            _event(confidence=1.5)

    def test_confidence_lower_bounds(self):
        with pytest.raises(ValidationError):
            _event(confidence=-0.1)

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            _event(arbitrary_field="bad")


class TestEventEnvelope:
    """Verify that events are few envelope types, not user behavior enumeration."""

    def test_event_types_are_few(self):
        from autoad_researcher.assistant.events import AssistantEventType
        from typing import get_args
        types = set(get_args(AssistantEventType))
        assert len(types) <= 10  # v0.5 target: ~7
        # confirm we haven't accidentally grown to dozens
        assert "user_uploaded_pdf" not in types
        assert "user_changed_metric" not in types
        assert "user_wants_patchcore" not in types

    def test_router_labels_not_event_types(self):
        """Router labels are NOT event types — they're hints for TransitionPolicy."""
        from autoad_researcher.assistant.events import RouterLabel
        from typing import get_args
        labels = set(get_args(RouterLabel))
        assert "correction" in labels
        assert "goal_update" in labels
        # Router labels should be helpers, not dozens of labels
        assert len(labels) <= 15
