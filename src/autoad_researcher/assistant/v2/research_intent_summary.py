"""Compact, user-transparent research intent summary persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.schemas.decisions import ConfirmedDecision


SUMMARY_FILE = "summary.json"


class BasedStatement(BaseModel):
    """A statement whose material or reasoning basis is explicit."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    basis: str = Field(min_length=1)


class ConfirmedTaskParameters(BaseModel):
    """User-provided or user-confirmed task values, without text extraction."""

    model_config = ConfigDict(extra="forbid")

    baseline: ConfirmedDecision | None = None
    dataset: ConfirmedDecision | None = None
    compute_budget: ConfirmedDecision | None = None
    primary_metrics: list[ConfirmedDecision] = Field(default_factory=list)
    evaluation_constraints: list[ConfirmedDecision] = Field(default_factory=list)


class ResearchIntentSummary(BaseModel):
    """Current research goal, facts, risks, and at most one blocker."""

    model_config = ConfigDict(extra="forbid")

    goal: str = ""
    confirmed_facts: list[str] = Field(default_factory=list)
    confirmed_task_parameters: ConfirmedTaskParameters = Field(default_factory=ConfirmedTaskParameters)
    inferred_facts: list[BasedStatement] = Field(default_factory=list)
    unresolved_conflicts: list[BasedStatement] = Field(default_factory=list)
    blocking_question: str | None = None


def load_research_intent_summary(run_dir: Path) -> ResearchIntentSummary | None:
    """Load the current summary when it exists and validates."""

    path = run_dir / SUMMARY_FILE
    if not path.is_file():
        return None
    return ResearchIntentSummary.model_validate_json(path.read_text(encoding="utf-8"))


def save_research_intent_summary(run_dir: Path, summary: ResearchIntentSummary) -> Path:
    """Atomically replace the current summary after schema validation."""

    path = run_dir / SUMMARY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(
        summary.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    try:
        with tmp.open("wb") as handle:
            handle.write(data.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return path
