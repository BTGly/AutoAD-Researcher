"""Readiness gate for a real, user-provided two-cycle PatchCore rehearsal.

This module does not invent scientific edits.  It validates two explicit patch
files against the pinned PatchCore repository, prevents protected evaluator
changes, and checks the real dataset/environment/weight prerequisites before a
physical benchmark run is admitted.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PatchCoreTwoCycleInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_path: Path
    expected_repository_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    dataset_root: Path
    required_dataset_paths: list[str] = Field(min_length=1)
    benchmark_python: Path
    lockfile_path: Path
    weight_path: Path
    expected_weight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_paths: list[str] = Field(min_length=1)
    cycle_1_patch: Path
    cycle_2_patch: Path

    @model_validator(mode="after")
    def _validate_relative_lists(self):
        for field_name in ("required_dataset_paths", "protected_paths"):
            for value in getattr(self, field_name):
                path = PurePosixPath(value)
                if path.is_absolute() or ".." in path.parts or value in {"", "."}:
                    raise ValueError(f"{field_name} entries must be relative")
        return self


class PatchCycleEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cycle: Literal[1, 2]
    patch_path: str
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_paths: list[str]


class PatchCoreTwoCycleReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready", "blocked"]
    blockers: list[str] = Field(default_factory=list)
    repository_commit: str | None = None
    cycle_evidence: list[PatchCycleEvidence] = Field(default_factory=list)


class PatchCoreTwoCycleReadinessChecker:
    """Run deterministic local checks only; no dataset or model execution occurs."""

    def check(self, inputs: PatchCoreTwoCycleInputs) -> PatchCoreTwoCycleReadiness:
        blockers: list[str] = []
        repository_commit = None
        cycle_evidence: list[PatchCycleEvidence] = []

        if not inputs.repository_path.is_dir():
            blockers.append("PatchCore repository directory is missing")
        elif not (inputs.repository_path / ".git").exists():
            blockers.append("PatchCore repository is not a Git checkout")
        else:
            repository_commit = self._git(inputs.repository_path, "rev-parse", "HEAD", allow_failure=True)
            if repository_commit != inputs.expected_repository_commit:
                blockers.append(
                    f"repository commit mismatch: expected {inputs.expected_repository_commit}, observed {repository_commit or 'unavailable'}"
                )
            dirty = self._git(inputs.repository_path, "status", "--porcelain", allow_failure=True)
            if dirty:
                blockers.append("PatchCore repository must be clean before rehearsal")

        for relative in inputs.required_dataset_paths:
            if not inputs.dataset_root.joinpath(*PurePosixPath(relative).parts).exists():
                blockers.append(f"dataset path is missing: {relative}")
        if not inputs.benchmark_python.is_file() or not os.access(inputs.benchmark_python, os.X_OK):
            blockers.append("benchmark Python interpreter is missing or not executable")
        if not inputs.lockfile_path.is_file():
            blockers.append("benchmark lockfile is missing")
        if not inputs.weight_path.is_file():
            blockers.append("offline PatchCore backbone weight is missing")
        elif _sha256(inputs.weight_path) != inputs.expected_weight_sha256:
            blockers.append("offline PatchCore backbone weight SHA-256 does not match")

        for cycle, patch in ((1, inputs.cycle_1_patch), (2, inputs.cycle_2_patch)):
            if not patch.is_file():
                blockers.append(f"cycle {cycle} patch is missing")
            elif patch.stat().st_size == 0:
                blockers.append(f"cycle {cycle} patch is empty")

        if not blockers and repository_commit is not None:
            sequence, sequence_blockers = self._validate_patch_sequence(inputs)
            cycle_evidence.extend(sequence)
            blockers.extend(sequence_blockers)

        return PatchCoreTwoCycleReadiness(
            status="blocked" if blockers else "ready",
            blockers=blockers,
            repository_commit=repository_commit,
            cycle_evidence=cycle_evidence,
        )

    def _validate_patch_sequence(
        self,
        inputs: PatchCoreTwoCycleInputs,
    ) -> tuple[list[PatchCycleEvidence], list[str]]:
        evidence: list[PatchCycleEvidence] = []
        blockers: list[str] = []
        protected = set(inputs.protected_paths)
        with tempfile.TemporaryDirectory(prefix="autoad-patchcore-rehearsal-") as temporary:
            worktree = Path(temporary) / "worktree"
            add = subprocess.run(
                [
                    "git",
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree),
                    inputs.expected_repository_commit,
                ],
                cwd=inputs.repository_path,
                text=True,
                capture_output=True,
                shell=False,
                check=False,
            )
            if add.returncode != 0:
                return [], [f"could not create rehearsal worktree: {add.stderr.strip()}"]
            try:
                for cycle, patch in ((1, inputs.cycle_1_patch), (2, inputs.cycle_2_patch)):
                    changed_paths = _patch_paths(worktree, patch)
                    protected_changes = sorted(protected.intersection(changed_paths))
                    if protected_changes:
                        blockers.append(
                            f"cycle {cycle} patch modifies protected paths: {protected_changes}"
                        )
                        break
                    check = subprocess.run(
                        ["git", "apply", "--check", str(patch.resolve())],
                        cwd=worktree,
                        text=True,
                        capture_output=True,
                        shell=False,
                        check=False,
                    )
                    if check.returncode != 0:
                        blockers.append(
                            f"cycle {cycle} patch does not apply after prior cycles: {check.stderr.strip()}"
                        )
                        break
                    apply = subprocess.run(
                        ["git", "apply", str(patch.resolve())],
                        cwd=worktree,
                        text=True,
                        capture_output=True,
                        shell=False,
                        check=False,
                    )
                    if apply.returncode != 0:
                        blockers.append(f"cycle {cycle} patch apply failed: {apply.stderr.strip()}")
                        break
                    evidence.append(
                        PatchCycleEvidence(
                            cycle=cycle,
                            patch_path=str(patch),
                            patch_sha256=_sha256(patch),
                            changed_paths=changed_paths,
                        )
                    )
            finally:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree)],
                    cwd=inputs.repository_path,
                    text=True,
                    capture_output=True,
                    shell=False,
                    check=False,
                )
        return evidence, blockers

    @staticmethod
    def _git(repository: Path, *args: str, allow_failure: bool = False) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repository,
            text=True,
            capture_output=True,
            shell=False,
            check=False,
        )
        if result.returncode != 0 and not allow_failure:
            raise RuntimeError(result.stderr.strip())
        return result.stdout.strip() if result.returncode == 0 else ""


def _patch_paths(repository: Path, patch: Path) -> list[str]:
    result = subprocess.run(
        ["git", "apply", "--numstat", str(patch.resolve())],
        cwd=repository,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        return []
    paths: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t", 2)
        if len(fields) == 3:
            paths.append(fields[2])
    return sorted(set(paths))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
