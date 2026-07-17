from __future__ import annotations

from pathlib import Path

import pytest

from autoad_researcher.experiment.cognitive_budget import CognitiveBudget, CognitiveBudgetStore
from autoad_researcher.experiment.coordinator import (
    ExploratoryCycleService,
    ExploratoryTrigger,
    IdeaCandidate,
    IdeaExplorerInvocation,
    IdeaExplorerResult,
)
from autoad_researcher.experiment.idea_tree import IdeaTreeStore
from autoad_researcher.experiment.session_store import ExperimentSessionStore


def _session(run_dir: Path) -> str:
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash="b" * 64,
        execution_mode="agent_assisted_after_approval",
    )
    IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)
    return session.session_id


def _budget(*, max_calls: int = 2, max_tokens: int = 100, max_wall_seconds: float = 20) -> CognitiveBudget:
    return CognitiveBudget(
        max_calls=max_calls,
        max_tokens=max_tokens,
        max_compact_cycles=1,
        max_exploratory_cycles=1,
        max_subagent_calls=1,
        max_wall_seconds=max_wall_seconds,
    )


def _candidate(axis: str, suffix: str) -> IdeaCandidate:
    return IdeaCandidate(
        mechanism=f"mechanism {suffix}",
        hypothesis=f"hypothesis {suffix}",
        observable=f"observable {suffix}",
        research_axis=axis,
        minimal_intervention=f"intervention {suffix}",
        falsification=f"falsification {suffix}",
        expected_cost="low",
        relationship_to_previous_ideas=f"relationship {suffix}",
        grounding=[f"evidence/{suffix}"],
    )


def test_exploratory_cycle_persists_multiple_differentiated_candidates_and_usage(tmp_path: Path):
    session_id = _session(tmp_path)
    calls = 0

    def explorer(_context, triggers):
        nonlocal calls
        calls += 1
        assert triggers[0].kind == "conflict"
        return IdeaExplorerInvocation(
            result=IdeaExplorerResult(candidates=[_candidate("regularization", "one"), _candidate("augmentation", "two")]),
            input_tokens=20,
            output_tokens=10,
            wall_seconds=2,
        )

    result = ExploratoryCycleService().run(
        tmp_path,
        session_id=session_id,
        cycle_id="explore_000001",
        parent_id="idea_000000",
        triggers=[ExploratoryTrigger(kind="conflict", rationale="two categories disagree", evidence_refs=["attempts/a/outcome_card.json"])],
        budget=_budget(),
        expected_input_tokens=20,
        expected_output_tokens=10,
        expected_wall_seconds=2,
        explorer=explorer,
    )

    assert calls == 1
    assert result.disposition == "explored"
    assert result.tree is not None and result.tree.revision == 1
    first = result.tree.node("idea_000001")
    assert first.research_axis == "regularization"
    assert first.minimal_intervention == "intervention one"
    assert first.falsification == "falsification one"
    assert len(CognitiveBudgetStore().load(tmp_path, session_id=session_id)) == 1


def test_exploratory_cycle_falls_back_without_call_when_structured_budget_preflight_rejects(tmp_path: Path):
    session_id = _session(tmp_path)
    calls = 0

    def explorer(_context, _triggers):
        nonlocal calls
        calls += 1
        raise AssertionError("budget fallback must not invoke explorer")

    result = ExploratoryCycleService().run(
        tmp_path,
        session_id=session_id,
        cycle_id="explore_budget",
        parent_id="idea_000000",
        triggers=[ExploratoryTrigger(kind="stagnation", rationale="no measurable improvement")],
        budget=_budget(max_calls=0),
        expected_input_tokens=20,
        expected_output_tokens=10,
        expected_wall_seconds=2,
        explorer=explorer,
    )
    assert calls == 0
    assert result.disposition == "fallback_compact"
    assert result.budget_check.exceeded_limits == ["max_calls"]
    assert IdeaTreeStore().load(tmp_path, session_id=session_id).revision == 0


def test_actual_overage_is_recorded_and_falls_back_without_tree_mutation(tmp_path: Path):
    session_id = _session(tmp_path)
    result = ExploratoryCycleService().run(
        tmp_path,
        session_id=session_id,
        cycle_id="explore_actual_overage",
        parent_id="idea_000000",
        triggers=[ExploratoryTrigger(kind="novel_literature_needed", rationale="existing sources cannot resolve mechanism")],
        budget=_budget(max_tokens=30),
        expected_input_tokens=10,
        expected_output_tokens=10,
        expected_wall_seconds=1,
        explorer=lambda _context, _triggers: IdeaExplorerInvocation(
            result=IdeaExplorerResult(candidates=[_candidate("axis-a", "one"), _candidate("axis-b", "two")]),
            input_tokens=20,
            output_tokens=20,
            wall_seconds=1,
        ),
    )
    assert result.disposition == "fallback_compact"
    assert result.budget_check.exceeded_limits == ["max_tokens"]
    assert len(CognitiveBudgetStore().load(tmp_path, session_id=session_id)) == 1
    assert IdeaTreeStore().load(tmp_path, session_id=session_id).revision == 0


def test_exploratory_cycle_requires_a_structured_trigger(tmp_path: Path):
    session_id = _session(tmp_path)
    with pytest.raises(ValueError, match="structured trigger"):
        ExploratoryCycleService().run(
            tmp_path,
            session_id=session_id,
            cycle_id="explore_no_trigger",
            parent_id="idea_000000",
            triggers=[],
            budget=_budget(),
            expected_input_tokens=1,
            expected_output_tokens=1,
            expected_wall_seconds=1,
            explorer=lambda _context, _triggers: None,
        )
