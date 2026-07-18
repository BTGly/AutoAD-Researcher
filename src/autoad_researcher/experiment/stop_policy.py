"""Deterministic validation of research-loop termination proposals."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.experiment.convergence import ConvergenceAlert


StopReason = Literal[
    "budget_exhausted",
    "wall_time_exhausted",
    "converged",
    "no_valid_frontier",
    "repeated_failure",
    "user_cancelled",
    "environment_unrecoverable",
    "coordinator_done_proposal_accepted",
]


class StopInputs(BaseModel):
    """Explicit facts used by StopPolicy; no status is inferred from prose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    compute_budget_remaining: float = Field(ge=0)
    cognitive_calls_remaining: int = Field(ge=0)
    cognitive_tokens_remaining: int = Field(ge=0)
    wall_seconds_remaining: float = Field(ge=0)
    valid_frontier_count: int = Field(ge=0)
    consecutive_terminal_failures: int = Field(ge=0)
    repeated_failure_limit: int = Field(default=3, gt=0)
    user_cancelled: bool = False
    environment_unrecoverable: bool = False
    coordinator_done_proposal: bool = False
    coordinator_done_proposal_approved: bool = False
    convergence_alert: ConvergenceAlert | None = None

    @model_validator(mode="after")
    def _validate_proposal(self):
        if self.coordinator_done_proposal_approved and not self.coordinator_done_proposal:
            raise ValueError("approval requires a Coordinator done proposal")
        if self.convergence_alert is not None and self.convergence_alert.session_id != self.session_id:
            raise ValueError("ConvergenceAlert session does not match StopInputs")
        return self


class StopDecision(BaseModel):
    """Immutable terminal decision or an explicit continue decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    session_id: str
    should_stop: bool
    reason: StopReason | None = None
    evidence: dict = Field(default_factory=dict)
    created_at: str

    @model_validator(mode="after")
    def _validate_reason(self):
        if self.should_stop != (self.reason is not None):
            raise ValueError("stop decision reason must exist exactly when should_stop is true")
        return self


class StopPolicy:
    """Validate termination in a fixed safety-first precedence order."""

    def evaluate(self, inputs: StopInputs) -> StopDecision:
        reason: StopReason | None = None
        evidence: dict = {}
        if inputs.user_cancelled:
            reason = "user_cancelled"
        elif inputs.environment_unrecoverable:
            reason = "environment_unrecoverable"
        elif inputs.compute_budget_remaining == 0 or inputs.cognitive_calls_remaining == 0 or inputs.cognitive_tokens_remaining == 0:
            reason = "budget_exhausted"
            evidence = {
                "compute_budget_remaining": inputs.compute_budget_remaining,
                "cognitive_calls_remaining": inputs.cognitive_calls_remaining,
                "cognitive_tokens_remaining": inputs.cognitive_tokens_remaining,
            }
        elif inputs.wall_seconds_remaining == 0:
            reason = "wall_time_exhausted"
        elif inputs.convergence_alert is not None and inputs.convergence_alert.level == "stop":
            reason = "converged"
            evidence = inputs.convergence_alert.model_dump(mode="json")
        elif inputs.valid_frontier_count == 0:
            reason = "no_valid_frontier"
        elif inputs.consecutive_terminal_failures >= inputs.repeated_failure_limit:
            reason = "repeated_failure"
            evidence = {
                "consecutive_terminal_failures": inputs.consecutive_terminal_failures,
                "repeated_failure_limit": inputs.repeated_failure_limit,
            }
        elif inputs.coordinator_done_proposal and inputs.coordinator_done_proposal_approved:
            reason = "coordinator_done_proposal_accepted"
        return StopDecision(
            session_id=inputs.session_id,
            should_stop=reason is not None,
            reason=reason,
            evidence=evidence,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def evaluate_and_persist(self, run_dir: Path, inputs: StopInputs) -> StopDecision:
        decision = self.evaluate(inputs)
        path = run_dir / "experiments" / "stops" / inputs.session_id / "decision.json"
        if path.is_file():
            existing = StopDecision.model_validate_json(path.read_text(encoding="utf-8"))
            if existing.should_stop:
                if existing.model_dump(exclude={"created_at"}) != decision.model_dump(exclude={"created_at"}):
                    raise ValueError("terminal StopDecision is immutable")
                return existing
            if not decision.should_stop:
                return existing
        _write_json_atomic(path, decision.model_dump(mode="json"))
        append_event(run_dir, "experiment.stop_policy.evaluated", decision.model_dump(mode="json"))
        return decision


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
