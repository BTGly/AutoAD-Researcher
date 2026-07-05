"""Tests for TransitionPolicy."""

from autoad_researcher.assistant.events import AssistantEvent
from autoad_researcher.assistant.session import AutoADAssistantSession
from autoad_researcher.assistant.transition_policy import apply, validate


def _event(event_type="user_input", **overrides):
    kwargs = {"event_id": "ev_001", "event_type": event_type}
    kwargs.update(overrides)
    return AssistantEvent(**kwargs)


def _session(mode="goal_alignment", **overrides):
    kwargs = {"session_id": "s_001", "run_id": "run_001", "mode": mode}
    kwargs.update(overrides)
    return AutoADAssistantSession(**kwargs)


# ── mode 迁移 ──


class TestTransitions:
    def test_goal_alignment_stays(self):
        s = _session("goal_alignment")
        s2 = apply(s, _event(router_labels=["goal_update"]))
        assert s2.mode == "goal_alignment"

    def test_correction_back_to_intent(self):
        s = _session("task_confirmation")
        s2 = apply(s, _event(router_labels=["correction"]))
        assert s2.mode == "intent_structuring"

    def test_confirmation_to_task_confirmation(self):
        s = _session("intent_structuring")
        s2 = apply(s, _event(router_labels=["confirmation"]))
        assert s2.mode == "task_confirmation"

    def test_rejection_to_intent(self):
        s = _session("task_confirmation")
        s2 = apply(s, _event(router_labels=["rejection"]))
        assert s2.mode == "intent_structuring"

    def test_revision_to_intent(self):
        s = _session("task_confirmation")
        s2 = apply(s, _event(router_labels=["revision_request"]))
        assert s2.mode == "intent_structuring"

    def test_source_input_at_pipeline_ready(self):
        s = _session("pipeline_ready")
        s2 = apply(s, _event(event_type="source_input"))
        assert s2.mode == "artifact_processing"

    def test_source_input_at_task_confirmation(self):
        s = _session("task_confirmation")
        s2 = apply(s, _event(event_type="source_input"))
        # source_input at task_confirmation stays — user is confirming a draft
        assert s2.mode == "task_confirmation"

    def test_progress_query_any_mode(self):
        s = _session("intent_structuring")
        s2 = apply(s, _event(event_type="progress_query"))
        assert s2.mode == "progress_reporting"

    def test_unknown_event_stays(self):
        s = _session("task_confirmation")
        s2 = apply(s, _event(event_type="unknown"))
        assert s2.mode == "task_confirmation"


    def test_goal_alignment_confirmation_without_draft_does_not_jump_to_task_confirmation(self):
        s = _session("goal_alignment")
        s2 = apply(s, _event(router_labels=["confirmation"]))
        assert s2.mode == "goal_alignment"
        assert s2.last_event_id == "ev_001"

    def test_goal_alignment_confirmation_with_draft_can_enter_task_confirmation(self):
        s = _session("goal_alignment")
        s.task.draft_ref = "task/research_task_draft.json"
        s2 = apply(s, _event(router_labels=["confirmation"]))
        assert s2.mode == "task_confirmation"

    def test_last_event_id_updated(self):
        s = _session()
        s2 = apply(s, _event())
        assert s2.last_event_id == "ev_001"


# ── 不变量 ──


class TestInvariants:
    def test_no_confirmed_task_no_pipeline(self):
        s = _session()
        s.task.ready_for_pipeline = True
        s.task.confirmed_ref = None
        v = validate(s)
        assert any("invariant_1" in m for m in v)

    def test_blocking_gaps_no_pipeline(self):
        s = _session()
        s.task.confirmed_ref = "task/confirmed.json"
        s.task.ready_for_pipeline = True
        s.task.has_blocking_gaps = True
        v = validate(s)
        assert any("invariant_2" in m for m in v)

    def test_execution_approved_requires_ready_for_pipeline(self):
        s = _session()
        s.task.execution_approved = True
        s.task.ready_for_pipeline = False
        v = validate(s)
        assert any("invariant_3" in m for m in v)

    def test_parsed_subset_of_registered(self):
        s = _session()
        s.sources.registered_ids = ["s1"]
        s.sources.parsed_ids = ["s2"]  # not registered
        v = validate(s)
        assert any("invariant_4" in m for m in v)

    def test_valid_session_no_violations(self):
        s = _session()
        s.task.confirmed_ref = "task/confirmed.json"
        s.task.has_blocking_gaps = False
        s.task.ready_for_pipeline = True
        v = validate(s)
        assert v == []


# ── 源 session 不可变 ──


class TestImmutability:
    def test_original_session_unchanged(self):
        s = _session("goal_alignment")
        _ = apply(s, _event(router_labels=["confirmation"]))
        # original session mode should not have changed
        assert s.mode == "goal_alignment"


# ── Task decision events ──


class TestTaskDecision:
    def test_revision_request_to_intent(self):
        s = _session("task_confirmation")
        s2 = apply(s, _event(event_type="task_decision", router_labels=["revision_request"]))
        assert s2.mode == "intent_structuring"

    def test_confirmation_to_task_confirmation(self):
        s = _session("intent_structuring")
        s2 = apply(s, _event(event_type="task_decision", router_labels=["confirmation"]))
        assert s2.mode == "task_confirmation"
