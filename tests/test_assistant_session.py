"""Tests for AutoADAssistantSession."""

import pytest
from pydantic import ValidationError

from autoad_researcher.assistant.session import (
    AutoADAssistantSession,
    SourceState,
    TaskControlState,
    InteractionState,
)


def _session(**overrides):
    kwargs = {"session_id": "s_001", "run_id": "run_001"}
    kwargs.update(overrides)
    return AutoADAssistantSession(**kwargs)


class TestSessionMinimal:
    def test_default_mode(self):
        s = _session()
        assert s.mode == "goal_alignment"

    def test_ready_for_pipeline_default_false(self):
        s = _session()
        assert s.task.ready_for_pipeline is False
        assert s.task.execution_approved is False

    def test_can_set_mode(self):
        s = _session(mode="intent_structuring")
        assert s.mode == "intent_structuring"

    def test_all_modes_valid(self):
        from autoad_researcher.assistant.session import AssistantMode
        from typing import get_args
        modes = set(get_args(AssistantMode))
        assert "goal_alignment" in modes
        assert "task_confirmation" in modes
        assert "pipeline_ready" in modes
        assert "progress_reporting" in modes
        assert len(modes) >= 6


class TestTaskControlSeparation:
    """ready_for_pipeline ≠ execution_approved"""

    def test_pipeline_and_execution_separate(self):
        s = _session()
        s.task.ready_for_pipeline = True
        assert s.task.execution_approved is False

    def test_track_draft_and_confirmed_separately(self):
        s = _session()
        s.task.draft_ref = "task/draft.json"
        s.task.confirmed_ref = None
        assert s.task.draft_ref is not None
        assert s.task.confirmed_ref is None

    def test_blocking_gaps_default_true(self):
        s = _session()
        assert s.task.has_blocking_gaps is True


class TestSessionCompact:
    """Session is minimal control state, not a big form."""

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            _session(user_full_text="lots of user text here")


class TestSourceState:
    def test_parsed_subset_of_registered(self):
        s = SourceState(
            registered_ids=["s1", "s2"],
            parsed_ids=["s1"],
            failed_ids=[],
        )
        assert "s1" in s.parsed_ids

    def test_defaults_empty(self):
        s = SourceState()
        assert s.source_ids == []
        assert s.registered_ids == []


class TestInteractionState:
    def test_defaults(self):
        s = InteractionState()
        assert s.pending_user_decision is None
        assert s.last_user_correction_ref is None
