"""Restricted Coordinator contracts and DeepAgents factory."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.experiment.cognition import CognitiveCommitStore
from autoad_researcher.experiment.idea_tree import IdeaTreeStore


class CycleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation: str = Field(min_length=1)
    comparison: str = Field(min_length=1)
    hypothesis_verdict: str = Field(min_length=1)
    keep_why: str = Field(min_length=1)
    failure_why: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    uncertainty: str = Field(min_length=1)
    next_action: Literal["add_child", "mark_ready", "prune", "request_user_decision", "stop"]
    target_node_id: str | None = None


class CoordinatorToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_dir: Path
    session_id: str


class CoordinatorTools:
    """Thin synchronous tool boundary; all mutations remain Store-owned."""
    def __init__(self, context: CoordinatorToolContext): self._context = context; self._trees = IdeaTreeStore(); self._commits = CognitiveCommitStore()
    def tree_view(self, node_id: str | None = None) -> dict:
        tree = self._trees.load(self._context.run_dir, session_id=self._context.session_id)
        if tree is None: raise FileNotFoundError("IdeaTree not found")
        node = tree.node(node_id) if node_id else None
        return (node.model_dump(mode="json") if node else tree.model_dump(mode="json"))
    def tree_add_node(self, *, expected_revision: int, idempotency_key: str, parent_id: str, mechanism: str, hypothesis: str, observable: str, grounding: list[str], expected_cost: str) -> dict:
        return self._trees.add_node(self._context.run_dir, session_id=self._context.session_id, expected_revision=expected_revision, idempotency_key=idempotency_key, parent_id=parent_id, mechanism=mechanism, hypothesis=hypothesis, observable=observable, grounding=grounding, expected_cost=expected_cost).model_dump(mode="json")
    def tree_prune(self, *, expected_revision: int, idempotency_key: str, node_id: str, reason: str) -> dict:
        return self._trees.request_prune(self._context.run_dir, session_id=self._context.session_id, expected_revision=expected_revision, idempotency_key=idempotency_key, node_id=node_id, reason=reason).model_dump(mode="json")
    def cognitive_ledger_read(self) -> list[dict]: return [item.model_dump(mode="json") for item in self._commits.load(self._context.run_dir, session_id=self._context.session_id)]


class CoordinatorAgentFactory:
    """One DeepAgents entrypoint, with no shell or direct persistence tools."""
    def create(self, *, model, tools: CoordinatorTools):
        from deepagents import create_deep_agent
        return create_deep_agent(model=model, tools=[tools.tree_view, tools.tree_add_node, tools.tree_prune, tools.cognitive_ledger_read], system_prompt="You are the AutoAD Research Coordinator. Use only supplied tools. Never execute shell commands, modify Git, or write arbitrary files. Return a CycleDecision.", response_format=CycleDecision, checkpointer=True)
