"""Explicit admission and frozen identity for an executable repository.

This module deliberately does not infer an execution target from a source kind,
directory name, or the presence of a repository checkout.  A caller must first
persist a user-authorized role assignment; admission then combines the existing
repository attestation and ExecutorAdapter evidence into one immutable binding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter
from autoad_researcher.repository_intelligence.acquisition import RepositoryAttestation
from autoad_researcher.schemas.decisions import ConfirmedDecision
from autoad_researcher.ui.sources import load_source_registry


RepositoryExecutionRole = Literal[
    "reference_only",
    "candidate_source_only",
    "executable",
]
RepositoryAdmissionCode = Literal[
    "execution_repository_unresolved",
    "execution_repository_attestation_invalid",
    "execution_adapter_unsupported",
]

ROLE_ASSIGNMENT_METADATA_KEY = "execution_repository_role_assignment"
_REPOSITORY_KINDS = frozenset({"github_repo", "local_repo"})


class RepositoryRoleAssignment(BaseModel):
    """A role selected by the user, not inferred from a material source."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    role: RepositoryExecutionRole
    authorization: ConfirmedDecision

    @model_validator(mode="after")
    def _require_matching_user_authorization(self) -> "RepositoryRoleAssignment":
        if self.authorization.value != self.source_id:
            raise ValueError("repository role authorization must name the selected source_id")
        return self


class ExecutionRepositoryBinding(BaseModel):
    """Frozen repository and adapter evidence consumed by later execution stages."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source_id: str = Field(min_length=1)
    source_kind: Literal["github_repo", "local_repo"]
    execution_role: Literal["executable"] = "executable"
    repository_ref: str = Field(min_length=1)
    repository_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation_ref: str = Field(min_length=1)
    attestation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adapter_manifest_ref: str = Field(min_length=1)
    adapter_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adapter_id: str = Field(min_length=1)
    adapter_evidence: dict[str, object]


class ExecutionRepositoryAdmission(BaseModel):
    """One deterministic admission decision; failures carry a stable code."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["admitted", "blocked"]
    binding: ExecutionRepositoryBinding | None = None
    code: RepositoryAdmissionCode | None = None
    blocker: str | None = None

    @model_validator(mode="after")
    def _align_result(self) -> "ExecutionRepositoryAdmission":
        if self.status == "admitted":
            if self.binding is None or self.code is not None or self.blocker is not None:
                raise ValueError("admitted repository requires only a binding")
        elif self.binding is not None or self.code is None or not self.blocker:
            raise ValueError("blocked repository requires code and blocker")
        return self


def assign_execution_repository_role(
    run_dir: Path,
    *,
    source_id: str,
    role: RepositoryExecutionRole,
    authorization: ConfirmedDecision,
) -> RepositoryRoleAssignment:
    """Persist a checked repository role assignment in the source registry.

    This is intentionally a small state update.  The dialogue gate is the
    future caller that supplies the confirmed authorization; the resolver never
    turns a reference or an arbitrary acquired checkout into an executable.
    """

    assignment = RepositoryRoleAssignment(
        source_id=source_id,
        role=role,
        authorization=authorization,
    )
    registry = load_source_registry(run_dir)
    source = next(
        (
            item
            for item in registry.get("sources", [])
            if isinstance(item, dict) and item.get("source_id") == source_id
        ),
        None,
    )
    if source is None:
        raise KeyError(f"source not found: {source_id}")
    if source.get("kind") not in _REPOSITORY_KINDS:
        raise ValueError("only registered repository sources can receive an execution role")

    from autoad_researcher.ui.sources import set_source_metadata

    set_source_metadata(
        run_dir,
        source_id,
        {ROLE_ASSIGNMENT_METADATA_KEY: assignment.model_dump(mode="json")},
    )
    return assignment


def resolve_execution_repository(run_dir: Path) -> ExecutionRepositoryAdmission:
    """Resolve exactly one explicitly executable repository into a binding."""

    assignments: list[tuple[dict[str, object], RepositoryRoleAssignment]] = []
    for raw_source in load_source_registry(run_dir).get("sources", []):
        if not isinstance(raw_source, dict):
            continue
        assignment = _role_assignment(raw_source)
        if assignment is not None and assignment.role == "executable":
            assignments.append((raw_source, assignment))

    if len(assignments) != 1:
        return _blocked(
            "execution_repository_unresolved",
            "exactly one user-authorized executable repository is required",
        )

    source, assignment = assignments[0]
    source_id = assignment.source_id
    source_kind = str(source.get("kind") or "")
    if source_kind not in _REPOSITORY_KINDS:
        return _blocked(
            "execution_repository_attestation_invalid",
            "the selected execution source is not a registered repository",
        )
    if source.get("intake_status") != "ok":
        return _blocked(
            "execution_repository_attestation_invalid",
            "the selected execution repository has not completed acquisition",
        )

    repository_ref = f"repos/{source_id}"
    repository_root = run_dir / repository_ref
    attestation_ref = f"repo_acquisition/{source_id}/repository_attestation.json"
    attestation_path = run_dir / attestation_ref
    try:
        _require_run_child(run_dir, repository_root)
        _require_run_child(run_dir, attestation_path)
        if not repository_root.is_dir() or not attestation_path.is_file():
            raise ValueError("repository checkout or attestation artifact is missing")
        attestation = RepositoryAttestation.model_validate_json(
            attestation_path.read_text(encoding="utf-8")
        )
        if attestation.source_id != source_id:
            raise ValueError("attestation source_id differs from the selected source")
    except Exception as exc:
        return _blocked("execution_repository_attestation_invalid", str(exc))

    adapter = ExecutorAdapter().inspect(repository_root)
    if adapter.status != "supported" or adapter.evidence is None or adapter.adapter_id is None:
        return _blocked(
            "execution_adapter_unsupported",
            adapter.blocker or "the selected repository has no supported executor adapter",
        )
    manifest_ref = f"{repository_ref}/autoad_executor_adapter.json"
    manifest_path = run_dir / manifest_ref
    if not manifest_path.is_file():  # Defensive: inspect() already checks this.
        return _blocked(
            "execution_adapter_unsupported",
            "the selected repository adapter manifest is missing",
        )

    return ExecutionRepositoryAdmission(
        status="admitted",
        binding=ExecutionRepositoryBinding(
            source_id=source_id,
            source_kind=source_kind,
            repository_ref=repository_ref,
            repository_fingerprint=attestation.tree_sha,
            attestation_ref=attestation_ref,
            attestation_sha256=attestation.attestation_sha256,
            adapter_manifest_ref=manifest_ref,
            adapter_manifest_sha256=sha256_file(manifest_path),
            adapter_id=adapter.adapter_id,
            adapter_evidence=adapter.evidence.model_dump(mode="json"),
        ),
    )


def _role_assignment(source: dict[str, object]) -> RepositoryRoleAssignment | None:
    metadata = source.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw_assignment = metadata.get(ROLE_ASSIGNMENT_METADATA_KEY)
    if not isinstance(raw_assignment, dict):
        return None
    try:
        assignment = RepositoryRoleAssignment.model_validate(raw_assignment)
    except Exception:
        return None
    return assignment if assignment.source_id == source.get("source_id") else None


def _require_run_child(run_dir: Path, path: Path) -> None:
    root = run_dir.resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError("repository artifact path escapes the run directory") from exc


def _blocked(code: RepositoryAdmissionCode, blocker: str) -> ExecutionRepositoryAdmission:
    return ExecutionRepositoryAdmission(status="blocked", code=code, blocker=blocker)
