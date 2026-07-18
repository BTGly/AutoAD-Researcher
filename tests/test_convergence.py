from pathlib import Path

import pytest

from autoad_researcher.experiment.convergence import (
    ConvergenceAttempt,
    ConvergenceConfig,
    ConvergenceMonitor,
    FailingCommand,
    ToolCall,
    repeated_failing_command_detected,
    repeated_patch_detected,
    repeated_step_detected,
    stable_stringify,
    step_signature,
)


def _attempt(index: int, *, purpose="exploration", effect="NO_EFFECT", delta=0.0, noise=0.01):
    return ConvergenceAttempt(
        attempt_id=f"attempt_{index:06d}",
        attempt_purpose=purpose,
        attempt_category="scientifically_evaluable",
        scientific_effect=effect,
        primary_delta=delta,
        noise_threshold=noise,
        research_axis="axis_a",
    )


def test_monitor_uses_tumbling_windows_and_separate_stop_counter():
    monitor = ConvergenceMonitor(
        ConvergenceConfig(
            window_size=5,
            warn_windows=1,
            paradigm_shift_windows=2,
            consecutive_no_progress_for_stop=15,
            noise_units_for_progress=1.0,
        )
    )
    attempts = [_attempt(index) for index in range(1, 11)]
    alert = monitor.evaluate(session_id="session", attempts=attempts)
    assert [window.attempt_ids for window in alert.windows] == [
        [f"attempt_{index:06d}" for index in range(1, 6)],
        [f"attempt_{index:06d}" for index in range(6, 11)],
    ]
    assert alert.level == "paradigm_shift"
    assert alert.consecutive_no_progress == 10

    stop = monitor.evaluate(session_id="session", attempts=[_attempt(index) for index in range(1, 16)])
    assert stop.level == "stop"
    assert stop.consecutive_no_progress == 15


def test_only_evaluable_exploration_attempts_count_and_progress_must_exceed_noise():
    monitor = ConvergenceMonitor(ConvergenceConfig(window_size=2, consecutive_no_progress_for_stop=4))
    attempts = [
        _attempt(1, purpose="baseline", effect="IMPROVEMENT", delta=1),
        _attempt(2, purpose="confirmation", effect="IMPROVEMENT", delta=1),
        _attempt(3, effect="IMPROVEMENT", delta=0.01, noise=0.01),
        _attempt(4, effect="IMPROVEMENT", delta=0.02, noise=0.01),
    ]
    alert = monitor.evaluate(session_id="session", attempts=attempts)
    assert len(alert.windows) == 1
    assert alert.windows[0].attempt_ids == ["attempt_000003", "attempt_000004"]
    assert alert.windows[0].improvement_count == 1
    assert alert.windows[0].velocity == pytest.approx(0.5)
    assert alert.consecutive_no_progress == 0


def test_alert_persistence_is_idempotent_for_same_scientific_state(tmp_path: Path):
    monitor = ConvergenceMonitor(ConvergenceConfig(window_size=2, warn_windows=1, paradigm_shift_windows=2))
    attempts = [_attempt(1), _attempt(2)]
    first = monitor.evaluate_and_persist(tmp_path, session_id="session", attempts=attempts, exhausted_axes=["axis_b"], duplicate_rate=0.4)
    second = monitor.evaluate_and_persist(tmp_path, session_id="session", attempts=attempts, exhausted_axes=["axis_b"], duplicate_rate=0.4)
    assert first.level == "warn"
    assert first.suggested_skills == ["revisit-pruned-lessons", "diversify-axes"]
    assert second.model_dump(exclude={"created_at"}) == first.model_dump(exclude={"created_at"})
    history = (tmp_path / "experiments" / "convergence" / "session" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(history) == 1


def test_lightweight_stuck_detectors_use_exact_structured_signatures():
    calls = [ToolCall(tool_name="tree_view", args={"node_id": "idea_000001", "depth": 1})]
    assert stable_stringify("tree_view", {"depth": 1, "node_id": "idea_000001"}) == stable_stringify("tree_view", {"node_id": "idea_000001", "depth": 1})
    assert step_signature(calls)
    assert repeated_step_detected([calls, calls, calls])
    assert not repeated_step_detected([calls, [ToolCall(tool_name="tree_view", args={"node_id": "idea_000002"})], calls])
    assert repeated_patch_detected(["a" * 64, "a" * 64])
    assert not repeated_patch_detected(["a" * 64, "b" * 64])
    assert repeated_failing_command_detected([
        FailingCommand(exit_code=1, stderr="same failure"),
        FailingCommand(exit_code=1, stderr="same failure"),
    ])
    assert not repeated_failing_command_detected([
        FailingCommand(exit_code=1, stderr="failure one"),
        FailingCommand(exit_code=1, stderr="failure two"),
    ])
