from __future__ import annotations

from pathlib import Path

import pytest

from autoad_researcher.experiment.cognition import CognitiveCommitStore
from autoad_researcher.experiment.coordinator import (
    CompactCycleService,
    ContextPruner,
    CoordinatorContextBuilder,
    CoordinatorContextMessage,
    CycleDecision,
)
from autoad_researcher.experiment.idea_tree import IdeaTreeMutation, IdeaTreeStore
from autoad_researcher.experiment.session_store import ExperimentSessionStore


def _session(run_dir: Path) -> str:
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash="a" * 64,
        execution_mode="agent_assisted_after_approval",
        budget={"max_calls": 4},
    )
    IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)
    return session.session_id


def test_context_pack_is_deterministic_and_keeps_authority_state_separate(tmp_path: Path):
    session_id = _session(tmp_path)
    builder = CoordinatorContextBuilder()
    first = builder.build(tmp_path, session_id=session_id)
    second = builder.build(tmp_path, session_id=session_id)
    assert first == second
    assert first.tree_revision == 0
    assert first.session_summary["session_id"] == session_id
    assert first.frontier_view[0]["node_id"] == "idea_000000"
    assert first.budget_snapshot == {"max_calls": 4}


def test_compact_cycle_calls_provider_once_applies_mutation_atomically_and_commits_then_prunes(tmp_path: Path):
    session_id = _session(tmp_path)
    calls = 0

    def decide(_context):
        nonlocal calls
        calls += 1
        return CycleDecision(
            observation="validation gain exceeded the prior result",
            comparison="candidate exceeds baseline",
            hypothesis_verdict="keep local mechanism",
            keep_why="the observed gain is reproducible enough to test once more",
            failure_why="no failure evidence in this cycle",
            confidence=0.8,
            uncertainty="only one result is available",
            next_action="add_child",
            target_node_id="idea_000000",
            evidence_refs=["attempts/attempt_000001/outcome_card.json"],
            mutations=[
                IdeaTreeMutation(
                    kind="add_child",
                    parent_id="idea_000000",
                    mechanism="change regularization",
                    hypothesis="less regularization improves validation score",
                    observable="validation score",
                    grounding=["attempts/attempt_000001/outcome_card.json"],
                    expected_cost="low",
                )
            ],
        )

    result = CompactCycleService().run(
        tmp_path,
        session_id=session_id,
        cycle_id="cycle_000001",
        observation="result received",
        ideation_focus="local follow-up",
        decision_provider=decide,
        model_profile="fake-model",
        prompt_version="coordinator-v1",
        working_context=[
            CoordinatorContextMessage(kind="scratch", content="discard me"),
            CoordinatorContextMessage(kind="tool_output", content="x" * 20, evidence_refs=["tool/ref"]),
            CoordinatorContextMessage(kind="decision", content="retain final decision", evidence_refs=["decision/ref"]),
        ],
        token_counter=lambda messages: sum(len(message.content) for message in messages),
        max_tool_output_chars=5,
    )

    assert calls == 1
    assert result.tree.revision == 1
    assert result.tree.node("idea_000001").hypothesis == "less regularization improves validation score"
    assert result.commit.tree_revision == 1
    assert result.commit.tree_mutations == ["add_child"]
    assert [message.kind for message in result.prune.messages] == ["tool_output", "decision"]
    assert result.prune.record.before_tokens == 51
    assert result.prune.record.after_tokens == 26
    assert CognitiveCommitStore().load(tmp_path, session_id=session_id)[0].commit_id == result.commit.commit_id


def test_failed_batch_mutation_does_not_persist_a_partial_tree_change(tmp_path: Path):
    session_id = _session(tmp_path)
    store = IdeaTreeStore()
    with pytest.raises(ValueError, match="IdeaTree root"):
        store.apply_mutations(
            tmp_path,
            session_id=session_id,
            expected_revision=0,
            idempotency_key="cycle:bad",
            mutations=[
                IdeaTreeMutation(
                    kind="add_child",
                    parent_id="idea_000000",
                    mechanism="valid first mutation",
                    hypothesis="valid first hypothesis",
                    observable="score",
                    expected_cost="low",
                ),
                IdeaTreeMutation(kind="prune", node_id="idea_000000", reason="invalid root prune"),
            ],
        )
    tree = store.load(tmp_path, session_id=session_id)
    assert tree is not None and tree.revision == 0 and len(tree.nodes) == 1


def test_pruner_records_exact_caller_supplied_token_counts(tmp_path: Path):
    session_id = _session(tmp_path)
    result = ContextPruner().prune(
        tmp_path,
        session_id=session_id,
        cycle_id="cycle_manual",
        messages=[CoordinatorContextMessage(kind="system", content="keep")],
        token_counter=lambda messages: len(messages),
        max_tool_output_chars=10,
    )
    assert result.record.before_tokens == result.record.after_tokens == 1
