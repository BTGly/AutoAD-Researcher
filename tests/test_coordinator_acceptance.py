from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoad_researcher.experiment.cognitive_budget import CognitiveBudget, CognitiveBudgetStore, new_usage
from autoad_researcher.experiment.cognition import CognitiveCommitStore
from autoad_researcher.experiment.coordinator import (
    CompactCycleService,
    CoordinatorContextBuilder,
    CoordinatorContextMessage,
    CycleDecision,
)
from autoad_researcher.experiment.coordinator_recovery import CoordinatorRecoveryService
from autoad_researcher.experiment.idea_tree import IdeaTreeMutation, IdeaTreeStore
from autoad_researcher.experiment.session_store import ExperimentSessionStore


def _session(run_dir: Path) -> str:
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash="d" * 64,
        execution_mode="agent_assisted_after_approval",
        budget={"configured_by": "acceptance fixture"},
    )
    IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)
    return session.session_id


def _add_decision(*, parent_id: str, mechanism: str, hypothesis: str, keep_why: str, failure_why: str) -> CycleDecision:
    return CycleDecision(
        observation=f"observation for {mechanism}",
        comparison=f"comparison for {mechanism}",
        hypothesis_verdict="continue with a falsifiable child",
        keep_why=keep_why,
        failure_why=failure_why,
        mechanism_interpretation=f"interpretation for {mechanism}",
        confidence=0.7,
        uncertainty="single controlled fixture result",
        next_action="add_child",
        target_node_id=parent_id,
        mutations=[IdeaTreeMutation(kind="add_child", parent_id=parent_id, mechanism=mechanism, hypothesis=hypothesis, observable="validation score", expected_cost="low")],
    )


def test_three_continuous_cycles_keep_prior_commit_reasoning_and_tree_lineage(tmp_path: Path):
    session_id = _session(tmp_path)
    cycles = CompactCycleService()
    first = cycles.run(
        tmp_path,
        session_id=session_id,
        cycle_id="continuous_1",
        observation="improvement",
        ideation_focus="local",
        decision_provider=lambda _context: _add_decision(parent_id="idea_000000", mechanism="improvement", hypothesis="controlled change improves score", keep_why="retain the measured improvement", failure_why="no prior regression"),
        model_profile="fixture", prompt_version="v1",
    )

    def second_decision(context):
        assert context.recent_cognitive_commits[-1]["keep_why"] == first.commit.keep_why
        return _add_decision(parent_id="idea_000001", mechanism="regression follow-up", hypothesis="isolate the regression source", keep_why="retain the earlier controlled gain", failure_why="the latest variant regressed relative to the retained gain")

    second = cycles.run(
        tmp_path,
        session_id=session_id,
        cycle_id="continuous_2",
        observation="regression",
        ideation_focus="failure interpretation",
        decision_provider=second_decision,
        model_profile="fixture", prompt_version="v1",
    )

    def third_decision(context):
        assert context.recent_cognitive_commits[-1]["failure_why"] == second.commit.failure_why
        return _add_decision(parent_id="idea_000002", mechanism="category conflict", hypothesis="separate category-specific mechanism", keep_why="preserve the causal evidence", failure_why="category results conflict and require a separate branch")

    third = cycles.run(
        tmp_path,
        session_id=session_id,
        cycle_id="continuous_3",
        observation="category conflict",
        ideation_focus="branch the hypothesis",
        decision_provider=third_decision,
        model_profile="fixture", prompt_version="v1",
    )
    tree = IdeaTreeStore().load(tmp_path, session_id=session_id)
    assert tree is not None
    assert [(node.node_id, node.parent_id) for node in tree.nodes[1:]] == [
        ("idea_000001", "idea_000000"),
        ("idea_000002", "idea_000001"),
        ("idea_000003", "idea_000002"),
    ]
    assert [commit.commit_id for commit in CognitiveCommitStore().load(tmp_path, session_id=session_id)] == [first.commit.commit_id, second.commit.commit_id, third.commit.commit_id]


def test_pruning_large_transient_outputs_preserves_authority_and_recovery_decision(tmp_path: Path):
    session_id = _session(tmp_path)
    messages = [CoordinatorContextMessage(kind="tool_output", content="x" * 10_000, evidence_refs=["tool/large"])] * 10
    messages.append(CoordinatorContextMessage(kind="decision", content="final decision", evidence_refs=["decision/final"]))
    result = CompactCycleService().run(
        tmp_path,
        session_id=session_id,
        cycle_id="prune_long_context",
        observation="stable observation",
        ideation_focus="compact",
        decision_provider=lambda _context: CycleDecision(observation="stable observation", comparison="no material change", hypothesis_verdict="pause", keep_why="authority remains available", failure_why="no new failure", confidence=0.9, uncertainty="none in fixture", next_action="stop"),
        model_profile="fixture", prompt_version="v1",
        working_context=messages,
        token_counter=lambda items: sum(len(item.content) for item in items),
        max_tool_output_chars=100,
    )
    assert result.prune is not None
    assert result.prune.record.before_tokens > 100_000
    assert result.prune.record.after_tokens == 1_014
    assert result.tree.revision == 0
    assert len(CognitiveCommitStore().load(tmp_path, session_id=session_id)) == 1
    recovered = CoordinatorRecoveryService().recover(tmp_path, session_id=session_id)
    assert recovered.checkpoint_action == "rebuild_from_authority"
    rebuilt = CoordinatorContextBuilder().build(tmp_path, session_id=session_id)
    assert rebuilt.recent_cognitive_commits[-1]["next_action"] == "stop"


def test_cognitive_budget_records_compact_and_exploratory_ratio_without_context_log_growth(tmp_path: Path):
    session_id = _session(tmp_path)
    budget = CognitiveBudget(max_calls=5, max_tokens=100, max_compact_cycles=4, max_exploratory_cycles=1, max_subagent_calls=1, max_wall_seconds=10)
    store = CognitiveBudgetStore()
    for index in range(4):
        assert store.append(tmp_path, session_id=session_id, budget=budget, usage=new_usage(cycle_id=f"compact_{index}", cycle_kind="compact", role="coordinator", input_tokens=5, output_tokens=5, wall_seconds=1)).allowed
    assert store.append(tmp_path, session_id=session_id, budget=budget, usage=new_usage(cycle_id="explore_1", cycle_kind="exploratory", role="idea_explorer", input_tokens=10, output_tokens=10, wall_seconds=2)).allowed
    usage = store.load(tmp_path, session_id=session_id)
    assert len(usage) == 5
    assert sum(item.cycle_kind == "compact" for item in usage) == 4
    assert sum(item.cycle_kind == "exploratory" for item in usage) == 1
    assert CoordinatorContextBuilder().build(tmp_path, session_id=session_id).recent_cognitive_commits == []
    assert json.loads((tmp_path / "experiments" / "cognition" / session_id / "llm_usage.jsonl").read_text().splitlines()[0])["cycle_kind"] == "compact"


def test_cycle_decision_contract_rejects_unknown_or_mismatched_actions_and_duplicate_ideas(tmp_path: Path):
    session_id = _session(tmp_path)
    with pytest.raises(ValueError, match="requires exactly one"):
        CycleDecision(observation="o", comparison="c", hypothesis_verdict="v", keep_why="k", failure_why="f", confidence=0.5, uncertainty="u", next_action="add_child")
    with pytest.raises(ValueError):
        CycleDecision.model_validate({"observation": "o", "comparison": "c", "hypothesis_verdict": "v", "keep_why": "k", "failure_why": "f", "confidence": 0.5, "uncertainty": "u", "next_action": "repair"})

    cycles = CompactCycleService()
    decision = _add_decision(parent_id="idea_000000", mechanism="duplicate mechanism", hypothesis="duplicate hypothesis", keep_why="initial", failure_why="none")
    cycles.run(tmp_path, session_id=session_id, cycle_id="duplicate_1", observation="first", ideation_focus="local", decision_provider=lambda _context: decision, model_profile="fixture", prompt_version="v1")
    with pytest.raises(ValueError, match="duplicate IdeaNode"):
        cycles.run(tmp_path, session_id=session_id, cycle_id="duplicate_2", observation="second", ideation_focus="local", decision_provider=lambda _context: decision, model_profile="fixture", prompt_version="v1")
    assert len(CognitiveCommitStore().load(tmp_path, session_id=session_id)) == 1
