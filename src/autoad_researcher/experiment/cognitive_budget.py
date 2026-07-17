"""Small, durable hard limits for Coordinator and specialist model calls."""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.event_service import append_event

COGNITIVE_BUDGET_DIR = "experiments/cognition"


class CognitiveBudget(BaseModel):
    """The plan-defined Session limits; callers supply this explicit contract."""

    model_config = ConfigDict(extra="forbid")

    max_calls: int = Field(ge=0)
    max_tokens: int = Field(ge=0)
    max_compact_cycles: int = Field(ge=0)
    max_exploratory_cycles: int = Field(ge=0)
    max_subagent_calls: int = Field(ge=0)
    max_wall_seconds: float = Field(ge=0)


class CognitiveUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    cycle_id: str = Field(min_length=1)
    cycle_kind: Literal["compact", "exploratory"]
    role: Literal["coordinator", "idea_explorer"]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    wall_seconds: float = Field(ge=0)
    created_at: str


class CognitiveBudgetCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    exceeded_limits: list[str]


class CognitiveBudgetStore:
    """Append usage records; each record is a real attempted model call."""

    def load(self, run_dir: Path, *, session_id: str) -> list[CognitiveUsage]:
        path = self._path(run_dir, session_id)
        if not path.is_file():
            return []
        return [CognitiveUsage.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def preflight(self, run_dir: Path, *, session_id: str, budget: CognitiveBudget, candidate: CognitiveUsage) -> CognitiveBudgetCheck:
        with self._lock(run_dir, session_id):
            return _evaluate(budget, [*self.load(run_dir, session_id=session_id), candidate])

    def append(self, run_dir: Path, *, session_id: str, budget: CognitiveBudget, usage: CognitiveUsage) -> CognitiveBudgetCheck:
        with self._lock(run_dir, session_id):
            existing = self.load(run_dir, session_id=session_id)
            check = _evaluate(budget, [*existing, usage])
            path = self._path(run_dir, session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(usage.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        append_event(run_dir, "experiment.cognitive_budget.usage_recorded", {"session_id": session_id, **usage.model_dump(mode="json"), "budget_allowed": check.allowed, "exceeded_limits": check.exceeded_limits})
        return check

    @staticmethod
    def _path(run_dir: Path, session_id: str) -> Path:
        return run_dir / COGNITIVE_BUDGET_DIR / session_id / "llm_usage.jsonl"

    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, session_id: str, timeout: float = 5.0):
        path = run_dir / COGNITIVE_BUDGET_DIR / session_id / ".llm_usage.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        fd: int | None = None
        while time.monotonic() < deadline:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                time.sleep(0.05)
        if fd is None:
            raise TimeoutError("could not acquire CognitiveBudget lock")
        try:
            yield
        finally:
            os.close(fd)
            try:
                path.unlink()
            except OSError:
                pass


def _evaluate(budget: CognitiveBudget, usage: list[CognitiveUsage]) -> CognitiveBudgetCheck:
    calls = len(usage)
    tokens = sum(item.input_tokens + item.output_tokens for item in usage)
    compact = sum(item.cycle_kind == "compact" for item in usage)
    exploratory = sum(item.cycle_kind == "exploratory" for item in usage)
    subagent = sum(item.role == "idea_explorer" for item in usage)
    wall = sum(item.wall_seconds for item in usage)
    exceeded = [
        name for name, actual, limit in (
            ("max_calls", calls, budget.max_calls),
            ("max_tokens", tokens, budget.max_tokens),
            ("max_compact_cycles", compact, budget.max_compact_cycles),
            ("max_exploratory_cycles", exploratory, budget.max_exploratory_cycles),
            ("max_subagent_calls", subagent, budget.max_subagent_calls),
            ("max_wall_seconds", wall, budget.max_wall_seconds),
        ) if actual > limit
    ]
    return CognitiveBudgetCheck(allowed=not exceeded, exceeded_limits=exceeded)


def new_usage(*, cycle_id: str, cycle_kind: Literal["compact", "exploratory"], role: Literal["coordinator", "idea_explorer"], input_tokens: int, output_tokens: int, wall_seconds: float) -> CognitiveUsage:
    return CognitiveUsage(cycle_id=cycle_id, cycle_kind=cycle_kind, role=role, input_tokens=input_tokens, output_tokens=output_tokens, wall_seconds=wall_seconds, created_at=datetime.now(timezone.utc).isoformat())
