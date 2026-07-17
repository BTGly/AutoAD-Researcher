from pathlib import Path
import pytest
from autoad_researcher.experiment.coordinator import CoordinatorToolContext, CoordinatorTools, CycleDecision
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.experiment.idea_tree import IdeaTreeStore

def _context(tmp_path: Path):
    session, _ = ExperimentSessionStore().create_or_get(tmp_path, task_ref="input_task.yaml", task_hash="a" * 64, execution_mode="agent_assisted_after_approval")
    IdeaTreeStore().create_or_get(tmp_path, session_id=session.session_id)
    return CoordinatorTools(CoordinatorToolContext(run_dir=tmp_path, session_id=session.session_id))

def test_tools_only_mutate_through_revisioned_store(tmp_path):
    tools = _context(tmp_path)
    tree = tools.tree_view(); assert tree["revision"] == 0
    added = tools.tree_add_node(expected_revision=0, idempotency_key="add", parent_id="idea_000000", mechanism="m", hypothesis="h", observable="o", grounding=[], expected_cost="low")
    assert added["revision"] == 1
    with pytest.raises(ValueError, match="revision conflict"):
        tools.tree_prune(expected_revision=0, idempotency_key="prune", node_id="idea_000001", reason="no")

def test_cycle_decision_rejects_extra_or_unstructured_actions():
    assert CycleDecision(observation="o", comparison="c", hypothesis_verdict="v", keep_why="k", failure_why="f", confidence=.5, uncertainty="u", next_action="stop").next_action == "stop"
    with pytest.raises(ValueError): CycleDecision.model_validate({"observation":"o", "comparison":"c", "hypothesis_verdict":"v", "keep_why":"k", "failure_why":"f", "confidence":.5, "uncertainty":"u", "next_action":"shell"})
