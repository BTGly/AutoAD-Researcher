"""Structured high-value Reflection layered on the existing CognitiveCommit ledger."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.experiment.cognition import CognitiveCommit, CognitiveCommitStore
from autoad_researcher.experiment.idea_tree import IdeaTree, IdeaTreeMutation, IdeaTreeStore


ReflectionTriggerKind = Literal[
    "seed_conflict",
    "primary_guardrail_conflict",
    "category_divergence",
    "high_value_improvement",
    "mechanism_inconsistency",
    "multi_branch_comparison",
    "persistent_inconclusive",
]


class ReflectionTrigger(BaseModel):
    """Structured reason for spending a specialist Reflection call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ReflectionTriggerKind
    rationale: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class DerivedHypothesis(BaseModel):
    """One falsifiable child proposal produced by Reflection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mechanism: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    observable: str = Field(min_length=1)
    research_axis: str = Field(min_length=1)
    minimal_intervention: str = Field(min_length=1)
    falsification: str = Field(min_length=1)
    relationship_to_previous_ideas: str = Field(min_length=1)
    expected_cost: Literal["unknown", "low", "medium", "high"]
    grounding: list[str] = Field(default_factory=list)


class ReflectionResult(BaseModel):
    """Specialist interpretation; deterministic validity gates remain external."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_effect: str = Field(min_length=1)
    mechanism_interpretation: str = Field(min_length=1)
    alternative_explanations: list[str] = Field(default_factory=list)
    implementation_concerns: list[str] = Field(default_factory=list)
    hypothesis_verdict: str = Field(min_length=1)
    keep_why: str = Field(min_length=1)
    failure_why: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    uncertainty: str = Field(min_length=1)
    reusable_property: str = Field(min_length=1)
    derived_hypotheses: list[DerivedHypothesis] = Field(default_factory=list)
    recommended_tree_action: Literal[
        "retain",
        "derive_child",
        "pivot",
        "prune",
        "request_user_decision",
        "stop_proposal",
    ]
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_action(self):
        if self.recommended_tree_action == "derive_child" and not self.derived_hypotheses:
            raise ValueError("derive_child requires at least one derived hypothesis")
        if self.recommended_tree_action != "derive_child" and self.derived_hypotheses:
            raise ValueError("derived hypotheses are only valid with derive_child")
        return self


class ReflectionInvocation(BaseModel):
    """Provider result with actual consumption evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: ReflectionResult
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    wall_seconds: float = Field(ge=0)


class ReflectionRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: ReflectionResult
    tree: IdeaTree
    commit: CognitiveCommit


class ReflectionAgentFactory:
    """Create a temporary specialist with no shell, Git, or persistence tools."""

    def create(self, *, model):
        from deepagents import create_deep_agent

        return create_deep_agent(
            model=model,
            system_prompt=(
                "You are AutoAD's Reflection specialist. Interpret only the supplied structured "
                "OutcomeCard, deterministic DecisionResult, NoiseFloor, Champion history, and IdeaTree "
                "context. Separate observed facts from mechanism hypotheses, preserve alternative "
                "explanations, and never claim that one failed implementation disproves a theory. "
                "Return ReflectionResult only; do not execute code or mutate files."
            ),
            response_format=ReflectionResult,
        )


class ReflectionService:
    """Persist one specialist reflection into the existing tree and commit ledger."""

    def __init__(
        self,
        *,
        tree_store: IdeaTreeStore | None = None,
        commit_store: CognitiveCommitStore | None = None,
    ):
        self._trees = tree_store or IdeaTreeStore()
        self._commits = commit_store or CognitiveCommitStore()

    def run(
        self,
        run_dir: Path,
        *,
        session_id: str,
        cycle_id: str,
        target_node_id: str,
        triggers: list[ReflectionTrigger],
        outcome_refs: list[str],
        provider: Callable[[IdeaTree, list[ReflectionTrigger]], ReflectionInvocation | ReflectionResult | dict],
        model_profile: str,
        prompt_version: str,
    ) -> ReflectionRun:
        if not triggers:
            raise ValueError("Reflection requires at least one structured trigger")
        tree = self._trees.load(run_dir, session_id=session_id)
        if tree is None:
            raise FileNotFoundError("IdeaTree not found")
        tree.node(target_node_id)
        raw = provider(tree, triggers)
        invocation = self._coerce_invocation(raw)
        result = invocation.result
        mutations = self._mutations(target_node_id, result)
        if mutations:
            tree = self._trees.apply_mutations(
                run_dir,
                session_id=session_id,
                expected_revision=tree.revision,
                idempotency_key=f"reflection-tree:{cycle_id}",
                mutations=mutations,
            )
        commit, _ = self._commits.append(
            run_dir,
            session_id=session_id,
            idempotency_key=f"reflection-commit:{cycle_id}",
            tree_revision=tree.revision,
            input_outcome_refs=outcome_refs,
            observation=result.observed_effect,
            comparison="; ".join(result.alternative_explanations) or "no competing explanation recorded",
            hypothesis_verdict=result.hypothesis_verdict,
            keep_why=result.keep_why,
            failure_why=result.failure_why,
            mechanism_interpretation=result.mechanism_interpretation,
            confidence=result.confidence,
            uncertainty=result.uncertainty,
            tree_mutations=[mutation.kind for mutation in mutations],
            next_action=result.recommended_tree_action,
            evidence_refs=sorted(
                {
                    *result.evidence_refs,
                    *(ref for trigger in triggers for ref in trigger.evidence_refs),
                }
            ),
            model_profile=model_profile,
            prompt_version=prompt_version,
        )
        return ReflectionRun(result=result, tree=tree, commit=commit)

    @staticmethod
    def _coerce_invocation(
        value: ReflectionInvocation | ReflectionResult | dict,
    ) -> ReflectionInvocation:
        if isinstance(value, ReflectionInvocation):
            return value
        if isinstance(value, ReflectionResult):
            return ReflectionInvocation(result=value, input_tokens=0, output_tokens=0, wall_seconds=0)
        if isinstance(value, dict) and "result" in value:
            return ReflectionInvocation.model_validate(value)
        return ReflectionInvocation(
            result=ReflectionResult.model_validate(value),
            input_tokens=0,
            output_tokens=0,
            wall_seconds=0,
        )

    @staticmethod
    def _mutations(target_node_id: str, result: ReflectionResult) -> list[IdeaTreeMutation]:
        if result.recommended_tree_action == "derive_child":
            return [
                IdeaTreeMutation(
                    kind="add_child",
                    parent_id=target_node_id,
                    mechanism=item.mechanism,
                    hypothesis=item.hypothesis,
                    observable=item.observable,
                    research_axis=item.research_axis,
                    minimal_intervention=item.minimal_intervention,
                    falsification=item.falsification,
                    relationship_to_previous_ideas=item.relationship_to_previous_ideas,
                    grounding=item.grounding,
                    expected_cost=item.expected_cost,
                )
                for item in result.derived_hypotheses
            ]
        if result.recommended_tree_action == "prune":
            return [
                IdeaTreeMutation(
                    kind="prune",
                    node_id=target_node_id,
                    reason=result.failure_why,
                )
            ]
        return []


def should_trigger_reflection(
    *,
    seed_conflict: bool = False,
    primary_guardrail_conflict: bool = False,
    category_divergence: bool = False,
    high_value_improvement: bool = False,
    mechanism_inconsistency: bool = False,
    multi_branch_comparison: bool = False,
    persistent_inconclusive: bool = False,
) -> list[ReflectionTriggerKind]:
    """Return exact plan-defined triggers; ordinary outcomes use base Reflection only."""

    flags: list[tuple[ReflectionTriggerKind, bool]] = [
        ("seed_conflict", seed_conflict),
        ("primary_guardrail_conflict", primary_guardrail_conflict),
        ("category_divergence", category_divergence),
        ("high_value_improvement", high_value_improvement),
        ("mechanism_inconsistency", mechanism_inconsistency),
        ("multi_branch_comparison", multi_branch_comparison),
        ("persistent_inconclusive", persistent_inconclusive),
    ]
    return [kind for kind, enabled in flags if enabled]
