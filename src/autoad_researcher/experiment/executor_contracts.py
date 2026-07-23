"""Frozen, deterministic inputs for one Executor-owned implementation attempt."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoad_researcher.benchmarks.hashing import sha256_file


class InterventionContract(BaseModel):
    """The scientific and filesystem boundary frozen before code changes."""

    model_config = ConfigDict(extra="forbid")

    idea_id: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    target_modules: list[str] = Field(min_length=1)
    allowed_paths: list[str] = Field(min_length=1)
    forbidden_paths: list[str] = Field(default_factory=list)
    allowed_parameters: list[str] | dict[str, Any] = Field(default_factory=list)
    evaluation_invariants: list[str] = Field(default_factory=list)
    time_budget: int = Field(gt=0)

    @field_validator("target_modules", "allowed_paths", "forbidden_paths")
    @classmethod
    def _validate_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            _relative_path(value)
        if len(set(values)) != len(values):
            raise ValueError("contract paths must not contain duplicates")
        return values

    @model_validator(mode="after")
    def _disjoint_path_policy(self) -> "InterventionContract":
        overlap = set(self.allowed_paths) & set(self.forbidden_paths)
        if overlap:
            raise ValueError("allowed_paths and forbidden_paths must be disjoint")
        return self


class WorkspaceSpec(BaseModel):
    """A reproducible worktree allocation for one Attempt."""

    model_config = ConfigDict(extra="forbid")

    base_commit: str = Field(min_length=1)
    worktree_path: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    protected_hashes: dict[str, str] = Field(min_length=1)
    environment_snapshot_ref: str = Field(min_length=1)

    @field_validator("protected_hashes")
    @classmethod
    def _validate_protected_hashes(cls, values: dict[str, str]) -> dict[str, str]:
        for path, digest in values.items():
            _relative_path(path)
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise ValueError("protected hashes must be lowercase SHA-256 digests")
        return values


def freeze_protected_hashes(worktree_path: Path, protected_paths: list[str]) -> dict[str, str]:
    """Hash precisely the paths declared by the caller; no directory inference."""

    root = worktree_path.resolve()
    hashes: dict[str, str] = {}
    for relative_path in sorted(protected_paths):
        path = _resolve_under(root, relative_path)
        if not path.is_file():
            raise FileNotFoundError(f"protected path does not exist: {relative_path}")
        hashes[relative_path] = sha256_file(path)
    return hashes


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part == ".." for part in path.parts) or not path.parts:
        raise ValueError("contract paths must be non-empty and relative")
    return value


def _resolve_under(root: Path, relative_path: str) -> Path:
    _relative_path(relative_path)
    resolved = root.joinpath(*PurePosixPath(relative_path).parts).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("path escapes declared worktree")
    return resolved
