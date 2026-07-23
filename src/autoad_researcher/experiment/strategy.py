"""Deterministic strategy eligibility; Coordinator remains the scientific chooser."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.experiment.convergence import ConvergenceAlert


class SkillDescriptor(BaseModel):
    """Explicit repository-local skill metadata supplied by the caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    directory_ref: str = Field(min_length=1)
    task_types: list[str] = Field(min_length=1)
    scope: Literal["axis", "subtree", "global"]
    effect_lifetime: Literal["attempt", "session"]
    requires_approval: bool = False
    affects_safety_constraints: bool = False

    @model_validator(mode="after")
    def _validate_directory_ref(self):
        path = PurePosixPath(self.directory_ref)
        if path.is_absolute() or ".." in path.parts or self.directory_ref in {"", "."}:
            raise ValueError("directory_ref must be a run-relative directory")
        return self


class StrategyContext(BaseModel):
    """Facts used only to filter and rank eligible skills."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: str = Field(min_length=1)
    disabled_skill_ids: set[str] = Field(default_factory=set)
    active_skill_ids: set[str] = Field(default_factory=set)
    approved_skill_ids: set[str] = Field(default_factory=set)
    repeated_skill_ids: set[str] = Field(default_factory=set)
    allow_safety_affecting_skills: bool = False


class SkillEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    rank: int | None = Field(default=None, ge=0)
    directory_ref: str


class StrategySelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    alert_level: Literal["none", "warn", "paradigm_shift", "stop"]
    evaluations: list[SkillEligibility]
    eligible_skill_candidates: list[str]
    eligible_skill_directories: list[str]
    created_at: str


class StrategySelector:
    """Filter and rank skills; never select one on the Coordinator's behalf."""

    def filter_and_rank(
        self,
        run_dir: Path,
        *,
        alert: ConvergenceAlert,
        skills: list[SkillDescriptor],
        context: StrategyContext,
        persist_audit: bool = True,
    ) -> StrategySelection:
        requested_order = {skill_id: index for index, skill_id in enumerate(alert.suggested_skills)}
        evaluations: list[SkillEligibility] = []
        for descriptor in skills:
            reasons: list[str] = []
            directory = run_dir.joinpath(*PurePosixPath(descriptor.directory_ref).parts)
            if not directory.is_dir() or not (directory / "SKILL.md").is_file():
                reasons.append("skill directory or SKILL.md is missing")
            if context.task_type not in descriptor.task_types:
                reasons.append("skill does not support the current task type")
            if descriptor.skill_id in context.disabled_skill_ids:
                reasons.append("skill is disabled")
            if descriptor.skill_id in context.active_skill_ids:
                reasons.append("skill is already active")
            if descriptor.skill_id in context.repeated_skill_ids:
                reasons.append("skill was already repeated consecutively")
            if descriptor.requires_approval and descriptor.skill_id not in context.approved_skill_ids:
                reasons.append("required approval is missing")
            if descriptor.affects_safety_constraints and not context.allow_safety_affecting_skills:
                reasons.append("skill would affect frozen safety constraints")
            if alert.level == "none" and descriptor.skill_id not in requested_order:
                reasons.append("no convergence signal requested this skill")
            rank = None if reasons else requested_order.get(descriptor.skill_id, len(requested_order))
            evaluations.append(
                SkillEligibility(
                    skill_id=descriptor.skill_id,
                    eligible=not reasons,
                    reasons=reasons,
                    rank=rank,
                    directory_ref=descriptor.directory_ref,
                )
            )
        eligible = sorted(
            (item for item in evaluations if item.eligible),
            key=lambda item: (item.rank if item.rank is not None else 10**9, item.skill_id),
        )
        selection = StrategySelection(
            alert_level=alert.level,
            evaluations=evaluations,
            eligible_skill_candidates=[item.skill_id for item in eligible],
            eligible_skill_directories=[item.directory_ref for item in eligible],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        if persist_audit:
            self._append_audit(run_dir, session_id=alert.session_id, selection=selection)
        return selection

    @staticmethod
    def resolve_selected_skill_directories(
        run_dir: Path,
        *,
        selection: StrategySelection,
        selected_skill_ids: list[str],
    ) -> list[str]:
        """Validate Coordinator choices and return exact directories for SkillsMiddleware."""

        eligible = {
            item.skill_id: item.directory_ref
            for item in selection.evaluations
            if item.eligible
        }
        unknown = [skill_id for skill_id in selected_skill_ids if skill_id not in eligible]
        if unknown:
            raise ValueError(f"Coordinator selected ineligible skill IDs: {unknown}")
        result: list[str] = []
        for skill_id in selected_skill_ids:
            ref = eligible[skill_id]
            directory = run_dir.joinpath(*PurePosixPath(ref).parts)
            if not directory.is_dir() or not (directory / "SKILL.md").is_file():
                raise FileNotFoundError(f"selected skill is no longer available: {skill_id}")
            result.append(str(directory))
        return result

    @staticmethod
    def _append_audit(run_dir: Path, *, session_id: str, selection: StrategySelection) -> None:
        path = run_dir / "experiments" / "strategies" / session_id / "selection_events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        identity = selection.model_dump(mode="json", exclude={"created_at"})
        previous = None
        if path.is_file():
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                previous = StrategySelection.model_validate_json(lines[-1]).model_dump(mode="json", exclude={"created_at"})
        if previous == identity:
            return
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(selection.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        append_event(run_dir, "experiment.strategy.filtered", {"session_id": session_id, **selection.model_dump(mode="json")})
