"""Restricted Coordinator contracts, deterministic cycles, and DeepAgents factory."""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.cognitive_budget import CognitiveBudget, CognitiveBudgetCheck, CognitiveBudgetStore, CognitiveUsage, new_usage
from autoad_researcher.experiment.cognition import CognitiveCommit, CognitiveCommitStore, ObservationSnapshot
from autoad_researcher.experiment.idea_tree import IdeaTree, IdeaTreeMutation, IdeaTreeStore
from autoad_researcher.experiment.noise_floor import NoiseFloorStore
from autoad_researcher.experiment.session_store import ExperimentSessionStore

COORDINATOR_DIR = "experiments/coordinator"


class CycleDecision(BaseModel):
    """The complete, schema-checked result of one cognitive boundary."""

    model_config = ConfigDict(extra="forbid")

    observation: str = Field(min_length=1)
    comparison: str = Field(min_length=1)
    hypothesis_verdict: str = Field(min_length=1)
    keep_why: str = Field(min_length=1)
    failure_why: str = Field(min_length=1)
    mechanism_interpretation: str = Field(default="no additional mechanism interpretation", min_length=1)
    confidence: float = Field(ge=0, le=1)
    uncertainty: str = Field(min_length=1)
    next_action: Literal["add_child", "mark_ready", "prune", "request_user_decision", "stop"]
    target_node_id: str | None = Field(default=None, pattern=r"^idea_[0-9]{6}$")
    mutations: list[IdeaTreeMutation] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_action_mutations(self):
        kinds = [mutation.kind for mutation in self.mutations]
        required = {
            "add_child": "add_child",
            "mark_ready": "mark_status",
            "prune": "prune",
        }
        expected = required.get(self.next_action)
        if expected is None:
            if kinds:
                raise ValueError("stop and request_user_decision decisions must not mutate the IdeaTree")
            return self
        if kinds != [expected]:
            raise ValueError(f"{self.next_action} decision requires exactly one {expected} mutation")
        mutation = self.mutations[0]
        if self.target_node_id is not None:
            mutation_target = mutation.parent_id if mutation.kind == "add_child" else mutation.node_id
            if mutation_target != self.target_node_id:
                raise ValueError("target_node_id must match the decision mutation target")
        if self.next_action == "mark_ready" and mutation.status != "READY":
            raise ValueError("mark_ready decision must set status READY")
        return self


class ContextPack(BaseModel):
    """A deterministic view of authority state supplied to one compact call."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    session_summary: dict[str, Any]
    tree_revision: int = Field(ge=0)
    frontier_view: list[dict[str, Any]]
    outcome_cards: list[dict[str, Any]]
    champion_summary: dict[str, Any] | None = None
    recent_cognitive_commits: list[dict[str, Any]]
    dead_end_summary: list[dict[str, Any]]
    noise_floor: dict[str, Any] | None = None
    budget_snapshot: dict[str, Any]
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(cls, **values: Any) -> "ContextPack":
        digest = canonical_sha256(values)
        return cls(**values, context_sha256=digest)


class CoordinatorContextMessage(BaseModel):
    """Transient message metadata; authority state never lives in this list."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["scratch", "tool_output", "decision", "system"]
    content: str
    evidence_refs: list[str] = Field(default_factory=list)


class ContextPruneRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    cycle_id: str = Field(min_length=1)
    before_tokens: int = Field(ge=0)
    after_tokens: int = Field(ge=0)
    preserved_refs: list[str]
    summary_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContextPruneResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[CoordinatorContextMessage]
    record: ContextPruneRecord


class CompactCycleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_pack: ContextPack
    decision: CycleDecision
    tree: IdeaTree
    commit: CognitiveCommit
    prune: ContextPruneResult | None = None


class ExploratoryTrigger(BaseModel):
    """Structured evidence for spending the larger exploratory budget."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["conflict", "stagnation", "low_confidence", "large_pivot", "high_value_result", "novel_literature_needed"]
    rationale: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class IdeaCandidate(BaseModel):
    """One differentiated exploratory proposal, before later Coordinator selection."""

    model_config = ConfigDict(extra="forbid")

    mechanism: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    observable: str = Field(min_length=1)
    research_axis: str = Field(min_length=1)
    minimal_intervention: str = Field(min_length=1)
    falsification: str = Field(min_length=1)
    expected_cost: Literal["unknown", "low", "medium", "high"]
    relationship_to_previous_ideas: str = Field(min_length=1)
    grounding: list[str] = Field(default_factory=list)


class IdeaExplorerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[IdeaCandidate] = Field(min_length=2)

    @model_validator(mode="after")
    def _validate_distinct_candidates(self):
        identities = {(item.mechanism, item.hypothesis) for item in self.candidates}
        if len(identities) != len(self.candidates):
            raise ValueError("IdeaExplorer candidates must be distinct")
        return self


class IdeaExplorerInvocation(BaseModel):
    """Explorer output plus provider-reported, recordable consumption."""

    model_config = ConfigDict(extra="forbid")

    result: IdeaExplorerResult
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    wall_seconds: float = Field(ge=0)


class ExploratoryCycleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disposition: Literal["explored", "fallback_compact"]
    context_pack: ContextPack
    candidates: list[IdeaCandidate] = Field(default_factory=list)
    tree: IdeaTree | None = None
    budget_check: CognitiveBudgetCheck
    fallback_reason: str | None = None


class CoordinatorToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_dir: Path
    session_id: str


class CoordinatorTools:
    """Thin synchronous tool boundary; all mutations remain Store-owned."""

    def __init__(self, context: CoordinatorToolContext):
        self._context = context
        self._trees = IdeaTreeStore()
        self._commits = CognitiveCommitStore()

    def tree_view(self, node_id: str | None = None) -> dict:
        tree = self._trees.load(self._context.run_dir, session_id=self._context.session_id)
        if tree is None:
            raise FileNotFoundError("IdeaTree not found")
        node = tree.node(node_id) if node_id else None
        return node.model_dump(mode="json") if node else tree.model_dump(mode="json")

    def tree_add_node(self, *, expected_revision: int, idempotency_key: str, parent_id: str, mechanism: str, hypothesis: str, observable: str, grounding: list[str], expected_cost: str) -> dict:
        return self._trees.add_node(self._context.run_dir, session_id=self._context.session_id, expected_revision=expected_revision, idempotency_key=idempotency_key, parent_id=parent_id, mechanism=mechanism, hypothesis=hypothesis, observable=observable, grounding=grounding, expected_cost=expected_cost).model_dump(mode="json")

    def tree_prune(self, *, expected_revision: int, idempotency_key: str, node_id: str, reason: str) -> dict:
        return self._trees.request_prune(self._context.run_dir, session_id=self._context.session_id, expected_revision=expected_revision, idempotency_key=idempotency_key, node_id=node_id, reason=reason).model_dump(mode="json")

    def cognitive_ledger_read(self) -> list[dict]:
        return [item.model_dump(mode="json") for item in self._commits.load(self._context.run_dir, session_id=self._context.session_id)]


class CoordinatorContextBuilder:
    """Build a stable compact input from existing Session, Tree, Attempt, and Commit stores."""

    def __init__(self, *, session_store: ExperimentSessionStore | None = None, tree_store: IdeaTreeStore | None = None, attempt_store: ExperimentAttemptStore | None = None, commit_store: CognitiveCommitStore | None = None, noise_store: NoiseFloorStore | None = None):
        self._sessions = session_store or ExperimentSessionStore()
        self._trees = tree_store or IdeaTreeStore()
        self._attempts = attempt_store or ExperimentAttemptStore()
        self._commits = commit_store or CognitiveCommitStore()
        self._noise = noise_store or NoiseFloorStore()

    def build(self, run_dir: Path, *, session_id: str, recent_commit_limit: int = 5) -> ContextPack:
        session = self._sessions.load(run_dir, session_id)
        tree = self._trees.load(run_dir, session_id=session_id)
        if session is None or tree is None:
            raise FileNotFoundError("Coordinator requires both ExperimentSession and IdeaTree")
        attempts = self._attempts.list_for_session(run_dir, session_id=session_id)
        commits = self._commits.load(run_dir, session_id=session_id)
        frontier_statuses = {"DRAFT", "REVIEWED", "READY", "RUNNING", "INCONCLUSIVE"}
        dead_end_statuses = {"PRUNED", "NOT_SUPPORTED"}
        outcomes = [
            {
                "attempt_id": attempt.attempt_id,
                "runtime_status": attempt.runtime_status,
                "failure_code": attempt.failure_code,
                "execution_result_ref": attempt.execution_result_ref,
                "retry_of": attempt.retry_of,
            }
            for attempt in attempts
        ]
        values = {
            "session_summary": session.model_dump(mode="json"),
            "tree_revision": tree.revision,
            "frontier_view": [self._node_summary(node) for node in tree.nodes if node.status in frontier_statuses],
            "outcome_cards": outcomes,
            "champion_summary": None,
            "recent_cognitive_commits": [item.model_dump(mode="json") for item in commits[-recent_commit_limit:]],
            "dead_end_summary": [self._node_summary(node) for node in tree.nodes if node.status in dead_end_statuses],
            "noise_floor": {f"{item.metric}:{item.category}": item.model_dump(mode="json") for item in self._noise.load_for_session(run_dir, session_id=session_id)} or None,
            "budget_snapshot": dict(sorted(session.budget.items())),
        }
        return ContextPack.create(**values)

    @staticmethod
    def _node_summary(node) -> dict[str, Any]:
        return {
            "node_id": node.node_id,
            "parent_id": node.parent_id,
            "status": node.status,
            "mechanism": node.mechanism,
            "hypothesis": node.hypothesis,
            "observable": node.observable,
            "research_axis": node.research_axis,
            "minimal_intervention": node.minimal_intervention,
            "falsification": node.falsification,
            "relationship_to_previous_ideas": node.relationship_to_previous_ideas,
            "expected_cost": node.expected_cost,
            "attempt_refs": node.attempt_refs,
            "evidence_refs": node.evidence_refs,
            "insights": [insight.model_dump(mode="json") for insight in node.insights],
        }


class ContextPruner:
    """Prune transient conversation material only after the durable commit exists."""

    def prune(self, run_dir: Path, *, session_id: str, cycle_id: str, messages: Sequence[CoordinatorContextMessage], token_counter: Callable[[Sequence[CoordinatorContextMessage]], int], max_tool_output_chars: int) -> ContextPruneResult:
        before_tokens = token_counter(messages)
        retained: list[CoordinatorContextMessage] = []
        refs: set[str] = set()
        for message in messages:
            if message.kind == "scratch":
                continue
            if message.kind == "tool_output" and len(message.content) > max_tool_output_chars:
                content = message.content[:max_tool_output_chars]
                message = message.model_copy(update={"content": content})
            retained.append(message)
            refs.update(message.evidence_refs)
        after_tokens = token_counter(retained)
        record = ContextPruneRecord(
            cycle_id=cycle_id,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            preserved_refs=sorted(refs),
            summary_hash=canonical_sha256({"messages": [item.model_dump(mode="json") for item in retained]}),
        )
        self._append_record(run_dir, session_id=session_id, record=record)
        append_event(run_dir, "experiment.coordinator.context_pruned", {"session_id": session_id, **record.model_dump(mode="json")})
        return ContextPruneResult(messages=retained, record=record)

    @staticmethod
    def _append_record(run_dir: Path, *, session_id: str, record: ContextPruneRecord) -> None:
        path = run_dir / COORDINATOR_DIR / session_id / "context_prune_events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class CompactCycleService:
    """Run one compact decision without making transient agent memory authoritative."""

    def __init__(self, *, context_builder: CoordinatorContextBuilder | None = None, tree_store: IdeaTreeStore | None = None, commit_store: CognitiveCommitStore | None = None, pruner: ContextPruner | None = None):
        self._contexts = context_builder or CoordinatorContextBuilder()
        self._trees = tree_store or IdeaTreeStore()
        self._commits = commit_store or CognitiveCommitStore()
        self._pruner = pruner or ContextPruner()

    def run(
        self,
        run_dir: Path,
        *,
        session_id: str,
        cycle_id: str,
        observation: str,
        ideation_focus: str,
        decision_provider: Callable[[ContextPack], CycleDecision | dict[str, Any]],
        model_profile: str,
        prompt_version: str,
        working_context: Sequence[CoordinatorContextMessage] | None = None,
        token_counter: Callable[[Sequence[CoordinatorContextMessage]], int] | None = None,
        max_tool_output_chars: int = 4_000,
    ) -> CompactCycleResult:
        context = self._contexts.build(run_dir, session_id=session_id)
        self._commits.write_observation_snapshot(
            run_dir,
            session_id=session_id,
            snapshot=ObservationSnapshot(
                cycle_id=cycle_id,
                tree_revision=context.tree_revision,
                outcome_refs=[item["execution_result_ref"] for item in context.outcome_cards if item["execution_result_ref"]],
                observation=observation,
                ideation_focus=ideation_focus,
                created_at=_utc_now(),
            ),
        )
        decision = CycleDecision.model_validate(decision_provider(context))
        tree = self._trees.load(run_dir, session_id=session_id)
        if tree is None or tree.revision != context.tree_revision:
            raise ValueError("IdeaTree revision changed during Compact Cycle")
        if decision.mutations:
            tree = self._trees.apply_mutations(
                run_dir,
                session_id=session_id,
                expected_revision=context.tree_revision,
                idempotency_key=f"compact:{cycle_id}:mutations",
                mutations=decision.mutations,
            )
        commit, _ = self._commits.append(
            run_dir,
            session_id=session_id,
            idempotency_key=f"compact:{cycle_id}:commit",
            tree_revision=tree.revision,
            input_outcome_refs=[item["execution_result_ref"] for item in context.outcome_cards if item["execution_result_ref"]],
            observation=decision.observation,
            comparison=decision.comparison,
            hypothesis_verdict=decision.hypothesis_verdict,
            keep_why=decision.keep_why,
            failure_why=decision.failure_why,
            mechanism_interpretation=decision.mechanism_interpretation,
            confidence=decision.confidence,
            uncertainty=decision.uncertainty,
            tree_mutations=[mutation.kind for mutation in decision.mutations],
            next_action=decision.next_action,
            evidence_refs=decision.evidence_refs,
            model_profile=model_profile,
            prompt_version=prompt_version,
        )
        prune = None
        if working_context is not None:
            if token_counter is None:
                raise ValueError("post-commit pruning requires an explicit token_counter")
            prune = self._pruner.prune(
                run_dir,
                session_id=session_id,
                cycle_id=cycle_id,
                messages=working_context,
                token_counter=token_counter,
                max_tool_output_chars=max_tool_output_chars,
            )
        append_event(run_dir, "experiment.coordinator.compact_cycle.committed", {"session_id": session_id, "cycle_id": cycle_id, "commit_id": commit.commit_id, "tree_revision": tree.revision, "context_sha256": context.context_sha256})
        return CompactCycleResult(context_pack=context, decision=decision, tree=tree, commit=commit, prune=prune)


class IdeaExplorerAgentFactory:
    """Create only the temporary specialist through the established DeepAgents factory."""

    def create(self, *, model):
        from deepagents import create_deep_agent

        return create_deep_agent(
            model=model,
            system_prompt=(
                "You are the AutoAD IdeaExplorer. Read the supplied accumulated ContextPack and "
                "return multiple differentiated IdeaCandidate proposals. Do not execute shell commands, "
                "modify Git, or persist files directly."
            ),
            response_format=IdeaExplorerResult,
        )


class ExploratoryCycleService:
    """Spend an explicitly admitted specialist call, or deterministically fall back."""

    def __init__(self, *, context_builder: CoordinatorContextBuilder | None = None, tree_store: IdeaTreeStore | None = None, budget_store: CognitiveBudgetStore | None = None):
        self._contexts = context_builder or CoordinatorContextBuilder()
        self._trees = tree_store or IdeaTreeStore()
        self._budget = budget_store or CognitiveBudgetStore()

    def run(
        self,
        run_dir: Path,
        *,
        session_id: str,
        cycle_id: str,
        parent_id: str,
        triggers: Sequence[ExploratoryTrigger],
        budget: CognitiveBudget,
        expected_input_tokens: int,
        expected_output_tokens: int,
        expected_wall_seconds: float,
        explorer: Callable[[ContextPack, Sequence[ExploratoryTrigger]], IdeaExplorerInvocation | dict[str, Any]],
    ) -> ExploratoryCycleResult:
        if not triggers:
            raise ValueError("Exploratory Cycle requires at least one structured trigger")
        context = self._contexts.build(run_dir, session_id=session_id)
        projected = new_usage(
            cycle_id=cycle_id,
            cycle_kind="exploratory",
            role="idea_explorer",
            input_tokens=expected_input_tokens,
            output_tokens=expected_output_tokens,
            wall_seconds=expected_wall_seconds,
        )
        preflight = self._budget.preflight(run_dir, session_id=session_id, budget=budget, candidate=projected)
        if not preflight.allowed:
            return self._fallback(run_dir, session_id=session_id, cycle_id=cycle_id, context=context, check=preflight, reason="CognitiveBudget preflight rejected exploratory call")
        invocation = IdeaExplorerInvocation.model_validate(explorer(context, triggers))
        actual = new_usage(
            cycle_id=cycle_id,
            cycle_kind="exploratory",
            role="idea_explorer",
            input_tokens=invocation.input_tokens,
            output_tokens=invocation.output_tokens,
            wall_seconds=invocation.wall_seconds,
        )
        actual_check = self._budget.append(run_dir, session_id=session_id, budget=budget, usage=actual)
        if not actual_check.allowed:
            return self._fallback(run_dir, session_id=session_id, cycle_id=cycle_id, context=context, check=actual_check, reason="CognitiveBudget actual usage exceeded the exploratory limit")
        tree = self._trees.apply_mutations(
            run_dir,
            session_id=session_id,
            expected_revision=context.tree_revision,
            idempotency_key=f"exploratory:{cycle_id}:candidates",
            mutations=[
                IdeaTreeMutation(
                    kind="add_child",
                    parent_id=parent_id,
                    mechanism=candidate.mechanism,
                    hypothesis=candidate.hypothesis,
                    observable=candidate.observable,
                    research_axis=candidate.research_axis,
                    minimal_intervention=candidate.minimal_intervention,
                    falsification=candidate.falsification,
                    relationship_to_previous_ideas=candidate.relationship_to_previous_ideas,
                    grounding=candidate.grounding,
                    expected_cost=candidate.expected_cost,
                )
                for candidate in invocation.result.candidates
            ],
        )
        append_event(run_dir, "experiment.coordinator.exploratory_cycle.committed", {
            "session_id": session_id,
            "cycle_id": cycle_id,
            "tree_revision": tree.revision,
            "trigger_kinds": [trigger.kind for trigger in triggers],
            "candidate_count": len(invocation.result.candidates),
        })
        return ExploratoryCycleResult(disposition="explored", context_pack=context, candidates=invocation.result.candidates, tree=tree, budget_check=actual_check)

    @staticmethod
    def _fallback(run_dir: Path, *, session_id: str, cycle_id: str, context: ContextPack, check: CognitiveBudgetCheck, reason: str) -> ExploratoryCycleResult:
        append_event(run_dir, "experiment.coordinator.exploratory_cycle.fallback", {"session_id": session_id, "cycle_id": cycle_id, "context_sha256": context.context_sha256, "reason": reason, "exceeded_limits": check.exceeded_limits})
        return ExploratoryCycleResult(disposition="fallback_compact", context_pack=context, budget_check=check, fallback_reason=reason)


class CoordinatorAgentFactory:
    """One DeepAgents entrypoint, with no shell or direct persistence tools."""

    def create(self, *, model, tools: CoordinatorTools):
        from deepagents import create_deep_agent

        return create_deep_agent(model=model, tools=[tools.tree_view, tools.tree_add_node, tools.tree_prune, tools.cognitive_ledger_read], system_prompt="You are the AutoAD Research Coordinator. Use only supplied tools. Never execute shell commands, modify Git, or write arbitrary files. Return a CycleDecision.", response_format=CycleDecision, checkpointer=True)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
