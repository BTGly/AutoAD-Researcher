"""Persisted, metric/category-aware baseline noise calibration."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NoiseFloor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    session_id: str
    metric: str
    category: str
    samples: list[float] = Field(default_factory=list)
    mean: float | None = None
    stddev: float | None = None
    threshold: float | None = None
    status: Literal["UNCALIBRATED", "PROVISIONAL_NOISE_FLOOR", "LOCKED", "LOCKED_MAX", "LOCKED_LOW_CONFIDENCE"]
    updated_at: str


def calibrate_noise_floor(*, session_id: str, metric: str, category: str, samples: list[float], budget_allows_three_runs: bool = True) -> NoiseFloor:
    if any(not math.isfinite(value) for value in samples):
        raise ValueError("noise calibration samples must be finite")
    count = len(samples)
    status = "UNCALIBRATED" if count < 3 or not budget_allows_three_runs else "PROVISIONAL_NOISE_FLOOR" if count < 5 else "LOCKED" if count < 7 else "LOCKED_MAX"
    average = mean(samples) if samples else None
    deviation = stdev(samples) if count > 1 else None
    return NoiseFloor(session_id=session_id, metric=metric, category=category, samples=samples, mean=average, stddev=deviation, threshold=None if deviation is None else 2 * deviation, status=status, updated_at=datetime.now(timezone.utc).isoformat())


class NoiseFloorStore:
    def save(self, run_dir: Path, floor: NoiseFloor) -> str:
        path = run_dir / "experiments" / "noise_floors" / floor.session_id / f"{floor.metric}__{floor.category}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(floor.model_dump_json(indent=2) + "\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        return str(path.relative_to(run_dir))

    def load_for_session(self, run_dir: Path, *, session_id: str) -> list[NoiseFloor]:
        directory = run_dir / "experiments" / "noise_floors" / session_id
        return [NoiseFloor.model_validate_json(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))] if directory.is_dir() else []
