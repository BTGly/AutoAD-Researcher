from pathlib import Path

import pytest

from autoad_researcher.experiment.convergence import ConvergenceAlert
from autoad_researcher.experiment.stop_policy import StopInputs, StopPolicy


def _inputs(**updates) -> StopInputs:
    values = {
        "session_id": "session_000001",
        "compute_budget_remaining": 1,
        "cognitive_calls_remaining": 1,
        "cognitive_tokens_remaining": 1,
        "wall_seconds_remaining": 1,
        "valid_frontier_count": 1,
        "consecutive_terminal_failures": 0,
    }
    values.update(updates)
    return StopInputs.model_validate(values)


def test_stop_policy_uses_deterministic_precedence():
    policy = StopPolicy()
    assert policy.evaluate(_inputs(user_cancelled=True, compute_budget_remaining=0)).reason == "user_cancelled"
    assert policy.evaluate(_inputs(environment_unrecoverable=True)).reason == "environment_unrecoverable"
    assert policy.evaluate(_inputs(cognitive_calls_remaining=0)).reason == "budget_exhausted"
    assert policy.evaluate(_inputs(wall_seconds_remaining=0)).reason == "wall_time_exhausted"
    assert policy.evaluate(_inputs(valid_frontier_count=0)).reason == "no_valid_frontier"
    assert policy.evaluate(_inputs(consecutive_terminal_failures=3)).reason == "repeated_failure"


def test_coordinator_done_is_only_a_proposal_until_approved():
    policy = StopPolicy()
    proposal = policy.evaluate(_inputs(coordinator_done_proposal=True))
    assert not proposal.should_stop
    accepted = policy.evaluate(_inputs(coordinator_done_proposal=True, coordinator_done_proposal_approved=True))
    assert accepted.reason == "coordinator_done_proposal_accepted"
    with pytest.raises(ValueError, match="approval requires"):
        _inputs(coordinator_done_proposal_approved=True)


def test_convergence_stop_requires_matching_session():
    alert = ConvergenceAlert(
        session_id="session_000001",
        level="stop",
        consecutive_no_progress=15,
        created_at="2026-07-18T00:00:00+00:00",
    )
    decision = StopPolicy().evaluate(_inputs(convergence_alert=alert))
    assert decision.reason == "converged"
    with pytest.raises(ValueError, match="session"):
        _inputs(convergence_alert=alert.model_copy(update={"session_id": "different"}))


def test_terminal_stop_decision_is_persisted_and_immutable(tmp_path: Path):
    policy = StopPolicy()
    first = policy.evaluate_and_persist(tmp_path, _inputs(user_cancelled=True))
    replay = policy.evaluate_and_persist(tmp_path, _inputs(user_cancelled=True))
    assert replay == first
    with pytest.raises(ValueError, match="immutable"):
        policy.evaluate_and_persist(tmp_path, _inputs(environment_unrecoverable=True))
