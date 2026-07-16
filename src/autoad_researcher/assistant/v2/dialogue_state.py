"""Bounded persistent state for V2 dialogue continuity."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.assistant.v2.research_dialogue_agent import GatedDialogueDecision
from autoad_researcher.assistant.v2.research_intent_summary import ResearchIntentSummary
from autoad_researcher.assistant.v2.task_bridge import BRIDGE_DIR, INPUT_TASK_FILE, PENDING_TASK_FILE
from autoad_researcher.ui.sources import load_source_registry


TRANSITIONS_DIR = "assistant"
TRANSITIONS_FILE = "v2_dialogue_transitions.jsonl"


class DialogueTransitionRecord(BaseModel):
    """One gated V2 decision whose reply summary was persisted successfully."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    created_at: str
    dialogue_mode: str
    policy_decision: str
    policy_category: str
    source_action: dict[str, Any] | None = None
    source_permission_decision: Literal["allow", "ask", "deny"] | None = None
    task_action: str | None = None
    execution_gate: str
    gate_notes: list[str] = Field(default_factory=list)
    summary_sha256: str = Field(min_length=64, max_length=64)


class PendingSourceAction(BaseModel):
    """A bounded projection of a queued/running source action."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    action: str
    job_id: str
    status: Literal["queued", "running"]


class SourceStateProjection(BaseModel):
    """Only parse-state fields that affect next-turn source routing."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    kind: str
    status: str
    active_parse_attempt_id: str | None = None
    parse_attempt_count: int = Field(ge=0)


class DialogueStateProjection(BaseModel):
    """Read-only, bounded continuity context supplied to both V2 LLM calls."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    previous_decision: DialogueTransitionRecord | None = None
    pending_source_actions: list[PendingSourceAction] = Field(default_factory=list)
    task_state: Literal["none", "pending_confirmation", "confirmed"] = "none"
    sources: list[SourceStateProjection] = Field(default_factory=list)


def append_dialogue_transition(
    run_dir: Path,
    *,
    decision: GatedDialogueDecision,
    summary: ResearchIntentSummary,
) -> DialogueTransitionRecord:
    """Append a transition only after the reply summary has been persisted."""
    record = DialogueTransitionRecord(
        created_at=datetime.now(timezone.utc).isoformat(),
        dialogue_mode=decision.dialogue_mode,
        policy_decision=decision.policy_assessment.decision,
        policy_category=decision.policy_assessment.category,
        source_action=(
            decision.source_action.model_dump(mode="json")
            if decision.source_action is not None
            else None
        ),
        source_permission_decision=(
            str(decision.source_permission.get("permission_decision"))
            if decision.source_permission is not None
            and decision.source_permission.get("permission_decision") in {"allow", "ask", "deny"}
            else None
        ),
        task_action=(
            decision.task_action.action
            if decision.task_action is not None
            else None
        ),
        execution_gate=decision.execution_gate,
        gate_notes=list(decision.gate_notes),
        summary_sha256=_summary_sha256(summary),
    )
    path = _transitions_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(
            (json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        )
        handle.flush()
        os.fsync(handle.fileno())
    return record


def build_dialogue_state_projection(run_dir: Path) -> DialogueStateProjection:
    """Read persisted decisions, job state, task state, and compact source state."""
    return DialogueStateProjection(
        previous_decision=_load_last_transition(run_dir),
        pending_source_actions=_pending_source_actions(run_dir),
        task_state=_task_state(run_dir),
        sources=_source_states(run_dir),
    )


def _transitions_path(run_dir: Path) -> Path:
    return run_dir / TRANSITIONS_DIR / TRANSITIONS_FILE


def _load_last_transition(run_dir: Path) -> DialogueTransitionRecord | None:
    path = _transitions_path(run_dir)
    if not path.is_file():
        return None
    last: DialogueTransitionRecord | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            last = DialogueTransitionRecord.model_validate_json(line)
        except ValueError:
            continue
    return last


def _pending_source_actions(run_dir: Path) -> list[PendingSourceAction]:
    actions: list[PendingSourceAction] = []
    for job in load_pipeline_jobs(run_dir):
        status = job.get("status")
        payload = job.get("payload")
        action = payload.get("requested_action") if isinstance(payload, dict) else None
        source_id = str(job.get("source_id") or "")
        if (
            status in {"queued", "running"}
            and isinstance(action, str)
            and source_id
            and job.get("job_id")
        ):
            actions.append(
                PendingSourceAction(
                    source_id=source_id,
                    action=action,
                    job_id=str(job["job_id"]),
                    status=status,
                )
            )
    return actions[:12]


def _task_state(run_dir: Path) -> Literal["none", "pending_confirmation", "confirmed"]:
    if (run_dir / INPUT_TASK_FILE).is_file():
        return "confirmed"
    if (run_dir / BRIDGE_DIR / PENDING_TASK_FILE).is_file():
        return "pending_confirmation"
    return "none"


def _source_states(run_dir: Path) -> list[SourceStateProjection]:
    states: list[SourceStateProjection] = []
    for source in load_source_registry(run_dir).get("sources", [])[:12]:
        if not isinstance(source, dict) or not source.get("source_id"):
            continue
        attempts = source.get("parse_attempts")
        states.append(
            SourceStateProjection(
                source_id=str(source["source_id"]),
                kind=str(source.get("kind") or ""),
                status=str(source.get("status") or ""),
                active_parse_attempt_id=(
                    str(source["active_parse_attempt_id"])
                    if source.get("active_parse_attempt_id")
                    else None
                ),
                parse_attempt_count=len(attempts) if isinstance(attempts, list) else 0,
            )
        )
    return states


def _summary_sha256(summary: ResearchIntentSummary) -> str:
    payload = json.dumps(
        summary.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
