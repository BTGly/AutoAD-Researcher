from pathlib import Path

import pytest

from autoad_researcher.experiment.cognition import CognitiveCommitStore, ObservationSnapshot
from autoad_researcher.experiment.idea_tree import IdeaTreeStore
from autoad_researcher.experiment.session_store import ExperimentSessionStore


def _session(run_dir: Path) -> str:
    session, _ = ExperimentSessionStore().create_or_get(run_dir, task_ref="input_task.yaml", task_hash="a" * 64, execution_mode="agent_assisted_after_approval")
    return session.session_id


def test_tree_mutations_are_revisioned_idempotent_and_append_only(tmp_path: Path):
    session_id = _session(tmp_path); store = IdeaTreeStore()
    tree, created = store.create_or_get(tmp_path, session_id=session_id)
    assert created and tree.revision == 0
    tree = store.add_node(tmp_path, session_id=session_id, expected_revision=0, idempotency_key="add:1", parent_id="idea_000000", mechanism="change optimizer", hypothesis="smaller step improves stability", observable="validation score", grounding=["sources/x"], expected_cost="low")
    assert tree.revision == 1
    replay = store.add_node(tmp_path, session_id=session_id, expected_revision=0, idempotency_key="add:1", parent_id="idea_000000", mechanism="change optimizer", hypothesis="smaller step improves stability", observable="validation score", grounding=["sources/x"], expected_cost="low")
    assert replay == tree
    with pytest.raises(ValueError, match="revision conflict"):
        store.mark_status(tmp_path, session_id=session_id, expected_revision=0, idempotency_key="status:1", node_id="idea_000001", status="REVIEWED")
    tree = store.append_reinterpretation(tmp_path, session_id=session_id, expected_revision=1, idempotency_key="interpret:1", node_id="idea_000001", text="first result is noisy", evidence_refs=["attempts/a/outcome_card.json"])
    assert tree.node("idea_000001").insights[0].text == "first result is noisy"


def test_tree_rejects_depth_transition_and_unsafe_prune(tmp_path: Path):
    session_id = _session(tmp_path); store = IdeaTreeStore(); tree, _ = store.create_or_get(tmp_path, session_id=session_id)
    for depth in range(1, 4):
        tree = store.add_node(tmp_path, session_id=session_id, expected_revision=tree.revision, idempotency_key=f"add:{depth}", parent_id=f"idea_{depth - 1:06d}", mechanism=f"m{depth}", hypothesis=f"h{depth}", observable=f"o{depth}", grounding=[], expected_cost="low")
    with pytest.raises(ValueError, match="maximum depth"):
        store.add_node(tmp_path, session_id=session_id, expected_revision=tree.revision, idempotency_key="add:4", parent_id="idea_000003", mechanism="m4", hypothesis="h4", observable="o4", grounding=[], expected_cost="low")
    with pytest.raises(ValueError, match="root"):
        store.request_prune(tmp_path, session_id=session_id, expected_revision=tree.revision, idempotency_key="prune:root", node_id="idea_000000", reason="no")


def test_cognitive_commit_is_immutable_and_snapshot_controls_recovery(tmp_path: Path):
    session_id = _session(tmp_path); ledger = CognitiveCommitStore()
    kwargs = dict(session_id=session_id, idempotency_key="cycle:1", tree_revision=2, input_outcome_refs=["attempts/a/outcome_card.json"], observation="improved", comparison="better than baseline", hypothesis_verdict="keep", keep_why="gain", failure_why="none", mechanism_interpretation="consistent", confidence=.8, uncertainty="one seed", tree_mutations=["idea_000001"], next_action="run confirmation", evidence_refs=["attempts/a/outcome_card.json"], model_profile="fake", prompt_version="v1")
    commit, created = ledger.append(tmp_path, **kwargs)
    assert created and ledger.append(tmp_path, **kwargs) == (commit, False)
    with pytest.raises(ValueError, match="different CognitiveCommit"):
        ledger.append(tmp_path, **{**kwargs, "observation": "different"})
    ledger.write_observation_snapshot(tmp_path, session_id=session_id, snapshot=ObservationSnapshot(cycle_id="cycle_1", tree_revision=2, outcome_refs=[], observation="observed", ideation_focus="local", created_at="2026-01-01T00:00:00+00:00"))
    assert ledger.recovery(tmp_path, session_id=session_id, tree_revision=2).action == "resume_ideation"
    assert ledger.recovery(tmp_path, session_id=session_id, tree_revision=3).action == "reobserve"
