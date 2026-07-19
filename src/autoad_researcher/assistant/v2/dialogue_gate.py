"""Deterministic validation between research decision and reply calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.research_dialogue_agent import (
    DialogueDecision,
    GatedDialogueDecision,
)
from autoad_researcher.assistant.v2.dialogue_permissions import (
    decide_source_action_permission,
    source_can_reparse,
)
from autoad_researcher.assistant.v2.research_intent_summary import ResearchIntentSummary
from autoad_researcher.assistant.v2.task_bridge import TaskInstruction
from autoad_researcher.assistant.v2.target_adapter import get_target_adapter_registry
from autoad_researcher.tools import append_permission_decision


class DialogueGate:
    """Apply state, identifier, Adapter, and permission checks without NLP."""

    @classmethod
    def validate(
        cls,
        decision: DialogueDecision,
        *,
        run_dir: Path,
        registered_sources: list[dict[str, Any]],
    ) -> GatedDialogueDecision:
        mode = decision.dialogue_mode
        policy = decision.policy_assessment
        notes: list[str] = []
        decision_consistent = True
        action_policy = decision.policy

        if policy.decision == "reject":
            action_policy = "deny"
            if mode == "reject":
                mode = "ask"
                notes.append("legacy_reject_mode_normalized")
        elif mode == "reject":
            mode = "ask"
            decision_consistent = False
            notes.append("reject_mode_without_reject_policy_removed")
        if mode == "act_request":
            mode = "act"
            notes.append("legacy_act_request_mode_normalized")

        source_action = decision.source_action
        dataset_source = decision.dataset_source
        task_action = (
            TaskInstruction(action=decision.task_action)
            if decision.task_action is not None
            else None
        )
        target_spec = decision.target_spec
        actions_allowed = (
            decision.is_valid
            and decision_consistent
            and action_policy != "deny"
        )
        source_permission: dict[str, Any] | None = None
        if not actions_allowed:
            source_action = None
            dataset_source = None
            task_action = None
            target_spec = None
        else:
            source_by_id = {
                str(item.get("source_id") or ""): item
                for item in registered_sources
                if item.get("source_id")
            }
            if source_action is not None:
                source = source_by_id.get(source_action.source_id)
                if source is None:
                    source_action = None
                    notes.append("unregistered_source_action_removed")
                elif (
                    source_action.action == "request_source_reparse"
                    and not source_can_reparse(source)
                ):
                    source_action = None
                    notes.append("source_reparse_unavailable")
                else:
                    permission = decide_source_action_permission(
                        run_dir=run_dir,
                        action=source_action,
                        source=source,
                    )
                    append_permission_decision(
                        run_dir / "assistant" / "permission_decisions.jsonl",
                        permission,
                    )
                    source_permission = permission.model_dump(mode="json")
                    if permission.permission_decision == "deny":
                        source_action = None
                        notes.append("source_action_permission_denied")
            if source_action is not None:
                dataset_source = None
                task_action = None
                target_spec = None
            elif mode not in {"ask", "plan"}:
                # Repository targets remain unavailable from an execution request.
                # A task-action proposal, however, is only a semantic handoff hint;
                # it is not an authorization and may still describe an act request.
                target_spec = None
            if target_spec is not None:
                resolved = get_target_adapter_registry().resolve(
                    target_spec.adapter_id,
                    target_spec.selectors,
                )
                if resolved is None:
                    target_spec = None
                    notes.append("invalid_target_spec_removed")

        execution_gate = "not_requested"
        action_scope = "none"
        if source_action is not None:
            action_scope = "source"
        elif target_spec is not None:
            action_scope = "repository"
        elif task_action is not None:
            action_scope = "experiment"
        if decision.action_scope != action_scope:
            notes.append("action_scope_normalized")

        if mode == "act" and source_action is None:
            execution_gate = (
                "blocked_dialogue_only"
                if (run_dir / "input_task.yaml").is_file()
                else "blocked_missing_contract"
            )

        return GatedDialogueDecision(
            dialogue_mode=mode,
            action_scope=action_scope,
            policy=action_policy,
            evidence_status=decision.evidence_status,
            conversation_transition=decision.conversation_transition,
            feasibility=decision.feasibility,
            numeric_claim_allowed=decision.numeric_claim_allowed,
            policy_assessment=policy,
            source_action=source_action,
            source_permission=source_permission,
            dataset_source=dataset_source,
            task_action=task_action,
            target_spec=target_spec,
            execution_gate=execution_gate,
            gate_notes=notes,
        )

    @staticmethod
    def task_action_allowed(
        decision: GatedDialogueDecision,
        summary: ResearchIntentSummary,
    ) -> bool:
        """Allow an explicit request to prepare a non-executing task draft."""
        return (
            decision.policy == "allow"
            and decision.task_action is not None
            and decision.source_action is None
            and bool(summary.goal.strip())
            and decision.conversation_transition != "cancel"
        )

    @staticmethod
    def missing_contract_execution_can_prepare_task(
        decision: GatedDialogueDecision,
        summary: ResearchIntentSummary,
    ) -> bool:
        """Allow an execution request to prepare, never confirm, its missing contract."""
        return (
            decision.dialogue_mode == "act"
            and decision.execution_gate == "blocked_missing_contract"
            and decision.policy == "allow"
            and decision.source_action is None
            and bool(summary.goal.strip())
            and summary.blocking_question is None
            and decision.conversation_transition != "cancel"
        )
