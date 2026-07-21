"""Derived summaries over the existing append-only CognitiveBudget ledger."""

from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.experiment.cognitive_budget import CognitiveBudget, CognitiveBudgetStore


class CognitiveCostSummary(BaseModel):
    """A rebuildable view; the usage JSONL remains the only cost authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    session_id: str
    total_calls: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_wall_seconds: float = Field(ge=0)
    compact_cycles: int = Field(ge=0)
    exploratory_cycles: int = Field(ge=0)
    coordinator_calls: int = Field(ge=0)
    specialist_calls: int = Field(ge=0)
    compact_to_exploratory_ratio: float | None = Field(default=None, ge=0)
    remaining_calls: int = Field(ge=0)
    remaining_tokens: int = Field(ge=0)
    remaining_wall_seconds: float = Field(ge=0)
    exceeded_limits: list[str] = Field(default_factory=list)
    cognitive_usage_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class CognitiveCostSummaryBuilder:
    def __init__(self, *, store: CognitiveBudgetStore | None = None):
        self._store = store or CognitiveBudgetStore()

    def build(
        self,
        run_dir: Path,
        *,
        session_id: str,
        budget: CognitiveBudget,
    ) -> CognitiveCostSummary:
        with self._store._lock(run_dir, session_id):
            usage = self._store.load(run_dir, session_id=session_id)
            usage_sha256 = _usage_sha256(run_dir, session_id)
        return _summary(session_id, budget, usage, usage_sha256)

    def build_and_persist(
        self,
        run_dir: Path,
        *,
        session_id: str,
        budget: CognitiveBudget,
    ) -> CognitiveCostSummary:
        """Persist a summary and the ledger fingerprint from one locked window."""

        with self._store._lock(run_dir, session_id):
            usage = self._store.load(run_dir, session_id=session_id)
            summary = _summary(session_id, budget, usage, _usage_sha256(run_dir, session_id))
            path = run_dir / "experiments" / "cognition" / session_id / "cost_summary.json"
            _write_json_atomic(path, summary.model_dump(mode="json"))
        return summary


def _summary(session_id: str, budget: CognitiveBudget, usage, usage_sha256: str) -> CognitiveCostSummary:
    calls = len(usage)
    tokens = sum(item.input_tokens + item.output_tokens for item in usage)
    wall = sum(item.wall_seconds for item in usage)
    compact = sum(item.cycle_kind == "compact" for item in usage)
    exploratory = sum(item.cycle_kind == "exploratory" for item in usage)
    coordinator = sum(item.role == "coordinator" for item in usage)
    specialists = calls - coordinator
    exceeded = []
    if calls > budget.max_calls:
        exceeded.append("max_calls")
    if tokens > budget.max_tokens:
        exceeded.append("max_tokens")
    if compact > budget.max_compact_cycles:
        exceeded.append("max_compact_cycles")
    if exploratory > budget.max_exploratory_cycles:
        exceeded.append("max_exploratory_cycles")
    if specialists > budget.max_subagent_calls:
        exceeded.append("max_subagent_calls")
    if wall > budget.max_wall_seconds:
        exceeded.append("max_wall_seconds")
    return CognitiveCostSummary(
        session_id=session_id,
        total_calls=calls,
        total_tokens=tokens,
        total_wall_seconds=wall,
        compact_cycles=compact,
        exploratory_cycles=exploratory,
        coordinator_calls=coordinator,
        specialist_calls=specialists,
        compact_to_exploratory_ratio=None if exploratory == 0 else compact / exploratory,
        remaining_calls=max(0, budget.max_calls - calls),
        remaining_tokens=max(0, budget.max_tokens - tokens),
        remaining_wall_seconds=max(0, budget.max_wall_seconds - wall),
        exceeded_limits=exceeded,
        cognitive_usage_sha256=usage_sha256,
    )


def _usage_sha256(run_dir: Path, session_id: str) -> str:
    path = CognitiveBudgetStore._path(run_dir, session_id)
    return hashlib.sha256(path.read_bytes() if path.is_file() else b"").hexdigest()


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
