from pathlib import Path

import pytest

from autoad_researcher.experiment.cognition import CognitiveCommitStore
from autoad_researcher.experiment.idea_tree import IdeaTreeStore
from autoad_researcher.experiment.reflection import (
    DerivedHypothesis,
    ReflectionResult,
    ReflectionService,
    ReflectionTrigger,
    should_trigger_reflection,
)
from autoad_researcher.experiment.session_store import ExperimentSessionStore


def _session(run_dir: Path) -> str:
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash="d" * 64,
        execution_mode="agent_assisted_after_approval",
        budget={"configured_by": "reflection fixture"},
    )
    IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)
    return session.session_id


def _result(*, action="derive_child") -> ReflectionResult:
    hypotheses = []
    if action == "derive_child":
        hypotheses = [
            DerivedHypothesis(
                mechanism="category-conditioned feature scale",
                hypothesis="a category-specific scale removes the observed conflict",
                observable="guardrail-safe AUROC improvement",
                research_axis="feature_scale",
                minimal_intervention="change one category-conditioned scalar",
                falsification="no improvement beyond the locked noise floor",
                relationship_to_previous_ideas="child explanation of the conflicting result",
                expected_cost="low",
                grounding=["attempts/attempt_000001/outcome_card.json"],
            )
        ]
    return ReflectionResult(
        observed_effect="primary metric improved while one category remained unstable",
        mechanism_interpretation="the aggregate gain may hide a category-conditioned failure mode",
        alternative_explanations=["seed variance", "checkpoint selection"],
        implementation_concerns=["activation evidence remains unverified"],
        hypothesis_verdict="retain the aggregate mechanism but isolate the category conflict",
        keep_why="the primary improvement exceeded the locked noise floor",
        failure_why="the category conflict prevents direct promotion",
        confidence=0.72,
        uncertainty="one category has only provisional replication",
        reusable_property="category-conditioned evaluation before promotion",
        derived_hypotheses=hypotheses,
        recommended_tree_action=action,
        evidence_refs=["attempts/attempt_000001/outcome_card.json"],
    )


def test_reflection_requires_structured_trigger_and_valid_action():
    assert should_trigger_reflection(seed_conflict=True, category_divergence=True) == [
        "seed_conflict",
        "category_divergence",
    ]
    with pytest.raises(ValueError, match="derived hypotheses"):
        ReflectionResult(
            observed_effect="effect",
            mechanism_interpretation="interpretation",
            hypothesis_verdict="verdict",
            keep_why="keep",
            failure_why="failure",
            confidence=0.5,
            uncertainty="uncertain",
            reusable_property="property",
            derived_hypotheses=[
                DerivedHypothesis(
                    mechanism="m",
                    hypothesis="h",
                    observable="o",
                    research_axis="a",
                    minimal_intervention="i",
                    falsification="f",
                    relationship_to_previous_ideas="r",
                    expected_cost="low",
                )
            ],
            recommended_tree_action="retain",
        )


def test_reflection_adds_derived_child_and_cognitive_commit(tmp_path: Path):
    session_id = _session(tmp_path)
    service = ReflectionService()
    trigger = ReflectionTrigger(
        kind="category_divergence",
        rationale="category deltas disagree",
        evidence_refs=["attempts/attempt_000001/outcome_card.json"],
    )
    run = service.run(
        tmp_path,
        session_id=session_id,
        cycle_id="reflection_000001",
        target_node_id="idea_000000",
        triggers=[trigger],
        outcome_refs=["attempts/attempt_000001/outcome_card.json"],
        provider=lambda _tree, _triggers: _result(),
        model_profile="fixture",
        prompt_version="reflection-v1",
    )
    assert run.tree.node("idea_000001").parent_id == "idea_000000"
    assert run.tree.node("idea_000001").research_axis == "feature_scale"
    assert run.commit.keep_why == "the primary improvement exceeded the locked noise floor"
    assert run.commit.failure_why == "the category conflict prevents direct promotion"
    assert run.commit.tree_mutations == ["add_child"]
    assert CognitiveCommitStore().load(tmp_path, session_id=session_id) == [run.commit]

    replay = service.run(
        tmp_path,
        session_id=session_id,
        cycle_id="reflection_000001",
        target_node_id="idea_000000",
        triggers=[trigger],
        outcome_refs=["attempts/attempt_000001/outcome_card.json"],
        provider=lambda _tree, _triggers: _result(),
        model_profile="fixture",
        prompt_version="reflection-v1",
    )
    assert replay.commit == run.commit
    assert len(replay.tree.nodes) == 2


def test_reflection_without_tree_mutation_still_records_fact_inference_and_confidence(tmp_path: Path):
    session_id = _session(tmp_path)
    run = ReflectionService().run(
        tmp_path,
        session_id=session_id,
        cycle_id="reflection_000002",
        target_node_id="idea_000000",
        triggers=[ReflectionTrigger(kind="high_value_improvement", rationale="large improvement")],
        outcome_refs=["attempts/attempt_000002/outcome_card.json"],
        provider=lambda _tree, _triggers: _result(action="retain"),
        model_profile="fixture",
        prompt_version="reflection-v1",
    )
    assert run.tree.revision == 0
    assert run.commit.next_action == "retain"
    assert run.commit.confidence == pytest.approx(0.72)
