"""Immutable provenance for multi-seed noise calibration.

Calibration may be assembled retrospectively from already immutable Attempts.
It is deliberately separate from an EvaluationContract: it documents the
cross-seed envelope without rewriting the contract each Attempt executed under.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.benchmarks.hashing import sha256_file


NOISE_CALIBRATION_PROTOCOLS_DIR = "experiments/noise_calibration_protocols"


def _relative_path(value: str, *, field_name: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value in {"", "."}:
        raise ValueError(f"{field_name} must be a non-empty relative path")
    return value


class NoiseCalibrationProtocol(BaseModel):
    """Frozen statement of which baseline variability may be calibrated."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    protocol_id: str = Field(pattern=r"^noise_calibration_[0-9]{6}$")
    session_id: str = Field(min_length=1)
    base_evaluation_contract_ref: str
    base_evaluation_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_seed_set: list[int] = Field(min_length=1)
    invariant_fields: list[str] = Field(min_length=1)
    variable_fields: list[Literal["seed", "PYTHONHASHSEED", "command_id seed component"]] = Field(min_length=1)
    included_attempts: list[str] = Field(min_length=1)
    excluded_attempts: list[str] = Field(default_factory=list)
    retrospective_or_prospective: Literal["retrospective", "prospective"]
    created_at: str

    @model_validator(mode="after")
    def _validate_protocol(self):
        _relative_path(self.base_evaluation_contract_ref, field_name="base_evaluation_contract_ref")
        if len(self.allowed_seed_set) != len(set(self.allowed_seed_set)):
            raise ValueError("allowed_seed_set must be unique")
        if len(self.invariant_fields) != len(set(self.invariant_fields)):
            raise ValueError("invariant_fields must be unique")
        if len(self.variable_fields) != len(set(self.variable_fields)):
            raise ValueError("variable_fields must be unique")
        if len(self.included_attempts) != len(set(self.included_attempts)):
            raise ValueError("included_attempts must be unique")
        if len(self.excluded_attempts) != len(set(self.excluded_attempts)):
            raise ValueError("excluded_attempts must be unique")
        if set(self.included_attempts) & set(self.excluded_attempts):
            raise ValueError("included_attempts and excluded_attempts must not overlap")
        return self


class NoiseCalibrationProtocolStore:
    """Append-only calibration-protocol artifacts."""

    def freeze(self, run_dir: Path, *, protocol: NoiseCalibrationProtocol) -> str:
        directory = run_dir / NOISE_CALIBRATION_PROTOCOLS_DIR / protocol.session_id
        path = directory / f"{protocol.protocol_id}.json"
        ref = str(path.relative_to(run_dir))
        if path.is_file():
            existing = NoiseCalibrationProtocol.model_validate_json(path.read_text(encoding="utf-8"))
            if existing != protocol:
                raise ValueError("noise calibration protocol ID already exists with different content")
            return ref
        _write_json_atomic(path, protocol.model_dump(mode="json"))
        return ref

    def load(self, run_dir: Path, *, session_id: str, protocol_id: str) -> NoiseCalibrationProtocol:
        path = run_dir / NOISE_CALIBRATION_PROTOCOLS_DIR / session_id / f"{protocol_id}.json"
        return NoiseCalibrationProtocol.model_validate_json(path.read_text(encoding="utf-8"))


def new_protocol_created_at() -> str:
    return datetime.now(timezone.utc).isoformat()


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
