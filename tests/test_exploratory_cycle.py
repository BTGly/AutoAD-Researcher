from __future__ import annotations

from pathlib import Path

import pytest

from autoad_researcher.experiment.cognitive_budget import CognitiveUsageStore
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
        explorer=explorer,
    )

    assert calls == 1
    assert result.disposition == "explored"
    assert result.tree is not None and result.tree.revision == 1
    first = result.tree.node("idea_000001")
    assert first.research_axis == "regularization"
    assert first.minimal_intervention == "intervention one"
    assert first.falsification == "falsification one"
    assert len(CognitiveUsageStore().load(tmp_path, session_id=session_id)) == 1


def test_exploratory_cycle_does_not_reject_after_prior_usage(tmp_path: Path):
    session_id = _session(tmp_path)
    calls = 0

    def explorer(_context, _triggers):
        nonlocal calls
        calls += 1
        return IdeaExplorerInvocation(
            result=IdeaExplorerResult(candidates=[_candidate("axis-a", "one"), _candidate("axis-b", "two")]),
            input_tokens=20,
            output_tokens=10,
            wall_seconds=2,
        )

    result = ExploratoryCycleService().run(
        tmp_path,
        session_id=session_id,
        cycle_id="explore_budget",
        parent_id="idea_000000",
        triggers=[ExploratoryTrigger(kind="stagnation", rationale="no measurable improvement")],
        explorer=explorer,
    )
    assert calls == 1
    assert result.disposition == "explored"
    assert result.usage.output_tokens == 10
    assert IdeaTreeStore().load(tmp_path, session_id=session_id).revision == 1


def test_actual_usage_is_recorded_without_tree_fallback(tmp_path: Path):
    session_id = _session(tmp_path)
    result = ExploratoryCycleService().run(
        tmp_path,
        session_id=session_id,
        cycle_id="explore_actual_overage",
        parent_id="idea_000000",
        triggers=[ExploratoryTrigger(kind="novel_literature_needed", rationale="existing sources cannot resolve mechanism")],
        explorer=lambda _context, _triggers: IdeaExplorerInvocation(
            result=IdeaExplorerResult(candidates=[_candidate("axis-a", "one"), _candidate("axis-b", "two")]),
            input_tokens=20,
            output_tokens=20,
            wall_seconds=1,
        ),
    )
    assert result.disposition == "explored"
    assert len(CognitiveUsageStore().load(tmp_path, session_id=session_id)) == 1
    assert IdeaTreeStore().load(tmp_path, session_id=session_id).revision == 1


def test_exploratory_cycle_requires_a_structured_trigger(tmp_path: Path):
    session_id = _session(tmp_path)
    with pytest.raises(ValueError, match="structured trigger"):
        ExploratoryCycleService().run(
            tmp_path,
            session_id=session_id,
            cycle_id="explore_no_trigger",
            parent_id="idea_000000",
            triggers=[],
            explorer=lambda _context, _triggers: None,
        )
