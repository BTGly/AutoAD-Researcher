"""Deterministic convergence, stagnation, and lightweight stuck detection."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.event_service import append_event


class ConvergenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window_size: int = Field(default=5, gt=0)
    warn_windows: int = Field(default=1, gt=0)
    paradigm_shift_windows: int = Field(default=2, gt=0)
    consecutive_no_progress_for_stop: int = Field(default=15, gt=0)
    noise_units_for_progress: float = Field(default=1.0, ge=0)


class ConvergenceAttempt(BaseModel):
    """Only explicit scientific facts needed by ConvergenceMonitor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str = Field(min_length=1)
    attempt_purpose: Literal["baseline", "exploration", "confirmation", "noise_calibration", "repair"]
    attempt_category: Literal["scientifically_evaluable", "run_failed", "protocol_violated"]
    scientific_effect: Literal["IMPROVEMENT", "NO_EFFECT", "REGRESSION", "INCONCLUSIVE"] | None = None
    primary_delta: float | None = None
    noise_threshold: float | None = Field(default=None, ge=0)
    research_axis: str | None = None

    @property
    def counts_toward_convergence(self) -> bool:
        return self.attempt_purpose == "exploration" and self.attempt_category == "scientifically_evaluable"


class ConvergenceWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=0)
    attempt_ids: list[str]
    improvement_count: int = Field(ge=0)
    velocity: float = Field(ge=0, le=1)


class ConvergenceAlert(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    session_id: str = Field(min_length=1)
    level: Literal["none", "warn", "paradigm_shift", "stop"]
    windows: list[ConvergenceWindow] = Field(default_factory=list)
    consecutive_no_progress: int = Field(ge=0)
    exhausted_axes: list[str] = Field(default_factory=list)
    duplicate_rate: float = Field(default=0, ge=0, le=1)
    suggested_skills: list[str] = Field(default_factory=list)
    created_at: str


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1)
    args: dict


class FailingCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exit_code: int
    stderr: str


def stable_stringify(tool_name: str, args: dict) -> str:
    return json.dumps({"tool": tool_name, "args": args}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def step_signature(tool_calls: list[ToolCall]) -> str:
    return "\n".join(f"tool:{call.tool_name}:{stable_stringify(call.tool_name, call.args)}" for call in tool_calls)


def repeated_step_detected(history: list[list[ToolCall]], *, threshold: int = 3) -> bool:
    if threshold <= 1:
        raise ValueError("threshold must be greater than one")
    if len(history) < threshold:
        return False
    signatures = [step_signature(calls) for calls in history[-threshold:]]
    return bool(signatures[0]) and len(set(signatures)) == 1


def repeated_patch_detected(patch_sha256s: list[str]) -> bool:
    return len(patch_sha256s) >= 2 and bool(patch_sha256s[-1]) and patch_sha256s[-1] == patch_sha256s[-2]


def repeated_failing_command_detected(history: list[FailingCommand]) -> bool:
    if len(history) < 2:
        return False
    first, second = history[-2:]
    return first.exit_code == second.exit_code and hashlib.sha256(first.stderr.encode("utf-8")).digest() == hashlib.sha256(second.stderr.encode("utf-8")).digest()


class ConvergenceMonitor:
    """Use tumbling windows for diagnosis and a separate consecutive stop counter."""

    def __init__(self, config: ConvergenceConfig | None = None):
        self.config = config or ConvergenceConfig()

    def evaluate(
        self,
        *,
        session_id: str,
        attempts: list[ConvergenceAttempt],
        exhausted_axes: list[str] | None = None,
        duplicate_rate: float = 0,
    ) -> ConvergenceAlert:
        if not 0 <= duplicate_rate <= 1:
            raise ValueError("duplicate_rate must be between zero and one")
        evaluable = [attempt for attempt in attempts if attempt.counts_toward_convergence]
        progress = [self._is_progress(attempt) for attempt in evaluable]
        windows: list[ConvergenceWindow] = []
        for start in range(0, len(evaluable), self.config.window_size):
            members = evaluable[start : start + self.config.window_size]
            if len(members) < self.config.window_size:
                break
            flags = progress[start : start + self.config.window_size]
            improvements = sum(flags)
            windows.append(
                ConvergenceWindow(
                    index=len(windows),
                    attempt_ids=[item.attempt_id for item in members],
                    improvement_count=improvements,
                    velocity=improvements / self.config.window_size,
                )
            )
        consecutive = 0
        for made_progress in reversed(progress):
            if made_progress:
                break
            consecutive += 1
        stagnant_windows = 0
        for window in reversed(windows):
            if window.velocity != 0:
                break
            stagnant_windows += 1
        level: Literal["none", "warn", "paradigm_shift", "stop"] = "none"
        if consecutive >= self.config.consecutive_no_progress_for_stop:
            level = "stop"
        elif stagnant_windows >= self.config.paradigm_shift_windows:
            level = "paradigm_shift"
        elif stagnant_windows >= self.config.warn_windows:
            level = "warn"
        axes = sorted(set(exhausted_axes or []))
        skills: list[str] = []
        if level in {"warn", "paradigm_shift", "stop"}:
            skills.append("revisit-pruned-lessons")
        if axes or duplicate_rate >= 0.3 or level in {"paradigm_shift", "stop"}:
            skills.append("diversify-axes")
        return ConvergenceAlert(
            session_id=session_id,
            level=level,
            windows=windows,
            consecutive_no_progress=consecutive,
            exhausted_axes=axes,
            duplicate_rate=duplicate_rate,
            suggested_skills=skills,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def evaluate_and_persist(
        self,
        run_dir: Path,
        *,
        session_id: str,
        attempts: list[ConvergenceAttempt],
        exhausted_axes: list[str] | None = None,
        duplicate_rate: float = 0,
    ) -> ConvergenceAlert:
        alert = self.evaluate(
            session_id=session_id,
            attempts=attempts,
            exhausted_axes=exhausted_axes,
            duplicate_rate=duplicate_rate,
        )
        directory = run_dir / "experiments" / "convergence" / session_id
        directory.mkdir(parents=True, exist_ok=True)
        latest = directory / "latest.json"
        _write_json_atomic(latest, alert.model_dump(mode="json"))
        history = directory / "alerts.jsonl"
        identity = alert.model_dump(mode="json", exclude={"created_at"})
        previous_identity = None
        if history.is_file():
            lines = [line for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                previous_identity = ConvergenceAlert.model_validate_json(lines[-1]).model_dump(mode="json", exclude={"created_at"})
        if previous_identity != identity:
            with history.open("a", encoding="utf-8") as handle:
                handle.write(alert.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            append_event(run_dir, "experiment.convergence.alert", alert.model_dump(mode="json"))
        return alert

    def _is_progress(self, attempt: ConvergenceAttempt) -> bool:
        if attempt.scientific_effect != "IMPROVEMENT" or attempt.primary_delta is None:
            return False
        if attempt.noise_threshold is None:
            return False
        return attempt.primary_delta > attempt.noise_threshold * self.config.noise_units_for_progress


def _write_json_atomic(path: Path, payload: dict) -> None:
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
