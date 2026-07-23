"""Frozen, Session-scoped scientific evaluation contracts.

The internal benchmark contract remains intentionally scoped to internal
benchmark fixtures.  This module is the authority for a user-confirmed
ExperimentSession: it records the full scientific protocol once, gives it a
stable content hash, and never overwrites an earlier revision.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file


EVALUATION_CONTRACTS_DIR = "experiments/evaluation_contracts"


def _relative_path(value: str, *, field_name: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value in {"", "."}:
        raise ValueError(f"{field_name} must be a non-empty relative path")
    return value


class EvaluationMetric(BaseModel):
    """One primary or guardrail metric under the frozen protocol."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    direction: Literal["maximize", "minimize"]
    implementation_ref: str

    @model_validator(mode="after")
    def _validate_ref(self):
        _relative_path(self.implementation_ref, field_name="implementation_ref")
        return self


class EvaluationResourceBudget(BaseModel):
    """The execution budget frozen with the protocol, not inferred at runtime."""

    model_config = ConfigDict(extra="forbid")

    max_wall_seconds: int = Field(gt=0)
    max_gpu_seconds: int = Field(ge=0)


class EvaluationSeedPolicy(BaseModel):
    """Forward-frozen seed roles for an EvaluationContract v2 or later."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_calibration_seeds: list[int] = Field(min_length=1)
    exploration_seed: int
    confirmation_seed_policy: Literal["explicit"]

    @model_validator(mode="after")
    def _validate_seeds(self):
        if len(self.baseline_calibration_seeds) != len(set(self.baseline_calibration_seeds)):
            raise ValueError("baseline_calibration_seeds must be unique")
        if self.exploration_seed not in self.baseline_calibration_seeds:
            raise ValueError("exploration_seed must be included in baseline_calibration_seeds")
        return self


class EvaluationContract(BaseModel):
    """Immutable scientific protocol for one Session revision."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1, 2] = 1
    contract_id: str = Field(pattern=r"^evaluation_contract_[0-9]{6}$")
    session_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    baseline_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    dataset_identity: str = Field(min_length=1)
    split_identity: str = Field(min_length=1)
    b_dev_ref: str
    b_test_ref: str
    category_set: list[str] = Field(default_factory=list)
    metrics: list[EvaluationMetric] = Field(min_length=1)
    primary_metric: str = Field(min_length=1)
    guardrails: list[str] = Field(default_factory=list)
    aggregation: Literal["mean"]
    seeds: list[int] = Field(min_length=1)
    seed_policy: EvaluationSeedPolicy | None = None
    checkpoint_selection: str = Field(min_length=1)
    resource_budget: EvaluationResourceBudget
    protected_paths: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_protocol(self):
        _relative_path(self.b_dev_ref, field_name="b_dev_ref")
        _relative_path(self.b_test_ref, field_name="b_test_ref")
        metric_names = [metric.name for metric in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("metric names must be unique")
        if self.primary_metric not in metric_names:
            raise ValueError("primary_metric must name one of metrics")
        if not set(self.guardrails).issubset(metric_names):
            raise ValueError("guardrails must name metrics")
        if self.primary_metric in self.guardrails:
            raise ValueError("primary_metric cannot also be a guardrail")
        if len(self.seeds) != len(set(self.seeds)):
            raise ValueError("seeds must be unique")
        if self.schema_version == 1 and self.seed_policy is not None:
            raise ValueError("seed_policy requires EvaluationContract schema_version 2")
        if self.schema_version == 2:
            if self.seed_policy is None:
                raise ValueError("EvaluationContract schema_version 2 requires seed_policy")
            if self.seeds != self.seed_policy.baseline_calibration_seeds:
                raise ValueError("seeds must exactly match seed_policy.baseline_calibration_seeds")
        if len(self.category_set) != len(set(self.category_set)):
            raise ValueError("category_set must be unique")
        for path in self.protected_paths:
            _relative_path(path, field_name="protected_paths")
        if len(self.protected_paths) != len(set(self.protected_paths)):
            raise ValueError("protected_paths must be unique")
        return self

    @property
    def sha256(self) -> str:
        return canonical_sha256(self)


class FrozenEvaluationContract(BaseModel):
    """Stable reference returned after writing one immutable contract artifact."""

    model_config = ConfigDict(extra="forbid")

    contract: EvaluationContract
    ref: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationContractStore:
    """Revisioned contract artifacts with a Session-level current pointer."""

    def freeze(self, run_dir: Path, *, contract: EvaluationContract) -> FrozenEvaluationContract:
        with self._lock(run_dir):
            directory = self._directory(run_dir, contract.session_id)
            path = directory / f"{contract.contract_id}.json"
            ref = str(path.relative_to(run_dir))
            if path.is_file():
                existing = EvaluationContract.model_validate_json(path.read_text(encoding="utf-8"))
                if existing != contract:
                    raise ValueError("evaluation contract ID already exists with different content")
                return FrozenEvaluationContract(contract=existing, ref=ref, sha256=sha256_file(path))
            current = self._load_current_unlocked(run_dir, contract.session_id)
            if current is not None and contract.revision != current.contract.revision + 1:
                raise ValueError("evaluation contract revision must advance exactly once")
            if current is None and contract.revision != 0:
                raise ValueError("first evaluation contract revision must be zero")
            self._write_json_atomic(path, contract.model_dump(mode="json"))
            frozen = FrozenEvaluationContract(contract=contract, ref=ref, sha256=sha256_file(path))
            self._write_json_atomic(directory / "current.json", frozen.model_dump(mode="json"))
            return frozen

    def current(self, run_dir: Path, *, session_id: str) -> FrozenEvaluationContract | None:
        with self._lock(run_dir):
            return self._load_current_unlocked(run_dir, session_id)

    @staticmethod
    def _directory(run_dir: Path, session_id: str) -> Path:
        return run_dir / EVALUATION_CONTRACTS_DIR / session_id

    def _load_current_unlocked(self, run_dir: Path, session_id: str) -> FrozenEvaluationContract | None:
        path = self._directory(run_dir, session_id) / "current.json"
        return FrozenEvaluationContract.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    @staticmethod
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

    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, timeout: float = 5.0):
        path = run_dir / EVALUATION_CONTRACTS_DIR / ".lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        fd: int | None = None
        while time.monotonic() < deadline:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                time.sleep(0.02)
        if fd is None:
            raise TimeoutError("could not acquire evaluation-contract lock")
        try:
            yield
        finally:
            os.close(fd)
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def freeze_protected_artifacts(run_dir: Path, paths: list[str]) -> dict[str, str]:
    """Return exact pre-run SHA-256 values for the contract's protected paths."""

    hashes: dict[str, str] = {}
    for relative_path in sorted(paths):
        _relative_path(relative_path, field_name="protected path")
        path = run_dir.joinpath(*PurePosixPath(relative_path).parts)
        if not path.is_file():
            raise FileNotFoundError(f"protected artifact is missing: {relative_path}")
        hashes[relative_path] = sha256_file(path)
    return hashes
