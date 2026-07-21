"""Thin aggregate admission over the existing Executor edit gates."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.experiment.executor_agent import ExecutorSummary
from autoad_researcher.experiment.executor_contracts import InterventionContract, WorkspaceSpec, freeze_protected_hashes
from autoad_researcher.runner import ExperimentCommandPlan, experiment_command_sha256


class InterventionAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    allowed: bool
    code: str
    detail: str
    changed_files: list[str] = Field(default_factory=list)
    patch_ref: str | None = None
    patch_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    command_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class InterventionAdmissionService:
    """Admit only a real, bounded final diff before Attempt creation."""

    def admit(
        self,
        *,
        contract: InterventionContract,
        workspace: WorkspaceSpec,
        summary: ExecutorSummary,
        artifact_dir: Path,
        command_plan: ExperimentCommandPlan,
    ) -> InterventionAdmission:
        root = Path(workspace.worktree_path)
        if summary.status != "completed" or not summary.changed_files:
            return self._write(artifact_dir, InterventionAdmission(allowed=False, code="PATCH_EMPTY", detail="Executor did not produce changed files"))
        changed = sorted(set(_git(root, "diff", "--name-only").splitlines()) - {""})
        if not changed or changed != sorted(set(summary.changed_files)):
            return self._write(artifact_dir, InterventionAdmission(allowed=False, code="PATCH_MISMATCH", detail="final git diff does not exactly match Executor changed_files", changed_files=changed))
        if any(not _covered(path, contract.allowed_paths) or _covered(path, contract.forbidden_paths) for path in changed):
            return self._write(artifact_dir, InterventionAdmission(allowed=False, code="PATH_REJECTED", detail="final diff escapes InterventionContract path policy", changed_files=changed))
        if freeze_protected_hashes(root, sorted(workspace.protected_hashes)) != workspace.protected_hashes:
            return self._write(artifact_dir, InterventionAdmission(allowed=False, code="PROTECTED_HASH_CHANGED", detail="protected hashes changed after Executor completion", changed_files=changed))
        allowed_parameters = set(contract.allowed_parameters if isinstance(contract.allowed_parameters, list) else contract.allowed_parameters)
        if allowed_parameters and (not summary.changed_symbols or not set(summary.changed_symbols) <= allowed_parameters):
            return self._write(artifact_dir, InterventionAdmission(allowed=False, code="PARAMETER_REJECTED", detail="Executor changed symbols are outside allowed_parameters", changed_files=changed))
        final_patch = artifact_dir / "final_patch.diff"
        final_patch.parent.mkdir(parents=True, exist_ok=True)
        final_patch.write_text(_git(root, "diff", "--", *changed), encoding="utf-8")
        if not final_patch.read_text(encoding="utf-8").strip():
            return self._write(artifact_dir, InterventionAdmission(allowed=False, code="PATCH_EMPTY", detail="final patch diff is empty", changed_files=changed))
        admission = InterventionAdmission(
            allowed=True,
            code="ADMITTED",
            detail="final diff, parameter policy, protected hashes, and rebuilt command passed",
            changed_files=changed,
            patch_ref="final_patch.diff",
            patch_sha256=sha256_file(final_patch),
            command_sha256=experiment_command_sha256(command_plan),
        )
        return self._write(artifact_dir, admission)

    @staticmethod
    def _write(artifact_dir: Path, admission: InterventionAdmission) -> InterventionAdmission:
        path = artifact_dir / "intervention_admission.json"
        if path.is_file():
            existing = InterventionAdmission.model_validate_json(path.read_text(encoding="utf-8"))
            if existing != admission:
                raise ValueError("intervention admission changed for immutable executor evidence")
            return existing
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(admission.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return admission


def _covered(path: str, policies: list[str]) -> bool:
    return any(path == policy or path.startswith(policy.rstrip("/") + "/") for policy in policies)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, shell=False).stdout.strip()
