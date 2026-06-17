"""Repository-aware evidence middleware primitives."""

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.repository_intelligence.evidence_models import (
    EvidenceIndexRecord,
    EvidenceRef,
    FileEvidenceRef,
    RepositoryIdentityEvidenceRef,
    WebEvidenceRef,
)
from autoad_researcher.repository_intelligence.ids import (
    GitCommitPattern,
    IdentifierPattern,
    Sha256Pattern,
    validate_relative_path,
)
from autoad_researcher.repository_intelligence.models import RepositorySource


class EvidenceMiddlewareError(ValueError):
    """Raised when evidence middleware cannot produce safe evidence."""


class ActiveRepositoryContext(BaseModel):
    """Runtime context injected after repository attestation."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=IdentifierPattern)
    repository_root: Path
    resolved_commit: str | None = Field(default=None, pattern=GitCommitPattern)
    tree_sha: str = Field(pattern=Sha256Pattern)


class FileEvidenceRequest(BaseModel):
    """Input needed to record repository file evidence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(pattern=IdentifierPattern)
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    tool_call_id: str = Field(pattern=IdentifierPattern)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return validate_relative_path(value)


def create_file_evidence(
    *,
    context: ActiveRepositoryContext,
    request: FileEvidenceRequest,
) -> FileEvidenceRef:
    """Create repository-relative file evidence from an active repository context."""
    if request.end_line < request.start_line:
        raise EvidenceMiddlewareError("end_line must be >= start_line")

    root = context.repository_root.resolve()
    file_path = _resolve_repository_file(root, request.path)
    file_sha = sha256_file(file_path)
    snippet = _read_line_range(file_path, request.start_line, request.end_line)
    snippet_sha = hashlib.sha256(snippet).hexdigest()

    return FileEvidenceRef(
        source_kind="repository_file",
        evidence_id=request.evidence_id,
        source_id=context.source_id,
        repository_commit=context.resolved_commit,
        path=file_path.relative_to(root).as_posix(),
        file_sha256=file_sha,
        start_line=request.start_line,
        end_line=request.end_line,
        snippet_sha256=snippet_sha,
        tool_call_id=request.tool_call_id,
        trust_level="code_fact",
    )


def create_repository_identity_evidence(
    *,
    source: RepositorySource,
    evidence_id: str,
    attestation_sha256: str,
    tool_call_ids: list[str],
) -> RepositoryIdentityEvidenceRef:
    """Create repository identity evidence from an attested RepositorySource."""
    if not tool_call_ids:
        raise EvidenceMiddlewareError("repository identity evidence requires tool_call_ids")
    return RepositoryIdentityEvidenceRef(
        source_kind="repository_identity",
        evidence_id=evidence_id,
        source_id=source.source_id,
        canonical_remote_url=source.canonical_remote_url,
        resolved_commit=source.resolved_commit,
        tree_sha=source.tree_sha,
        detached_head=source.detached_head,
        dirty=source.dirty,
        attestation_sha256=attestation_sha256,
        tool_call_ids=tool_call_ids,
        trust_level="repository_identity",
    )


def append_evidence(index_path: Path, evidence: EvidenceRef) -> None:
    """Append one evidence record to an Evidence Index JSONL file."""
    _assert_evidence_safe(evidence)
    existing_ids = {record.evidence.evidence_id for record in read_evidence_index(index_path)}
    if evidence.evidence_id in existing_ids:
        raise EvidenceMiddlewareError(f"duplicate evidence_id: {evidence.evidence_id}")

    index_path.parent.mkdir(parents=True, exist_ok=True)
    record = EvidenceIndexRecord(schema_version=1, evidence=evidence)
    data = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    with index_path.open("ab") as f:
        f.write(data.encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())


def read_evidence_index(index_path: Path) -> list[EvidenceIndexRecord]:
    """Read an Evidence Index JSONL file."""
    if not index_path.exists():
        return []
    records: list[EvidenceIndexRecord] = []
    for line_number, line in enumerate(index_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(EvidenceIndexRecord.model_validate_json(line))
        except Exception as exc:
            raise EvidenceMiddlewareError(f"invalid evidence index line {line_number}") from exc
    return records


def _resolve_repository_file(root: Path, relative_path: str) -> Path:
    requested = Path(validate_relative_path(relative_path))
    candidate = root / requested
    _reject_symlink_components(root, candidate)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise EvidenceMiddlewareError(f"repository path escape: {relative_path}") from exc
    if not resolved.is_file():
        raise EvidenceMiddlewareError(f"repository evidence path is not a file: {relative_path}")
    return resolved


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise EvidenceMiddlewareError(f"symlink path forbidden: {current.relative_to(root).as_posix()}")


def _read_line_range(path: Path, start_line: int, end_line: int) -> bytes:
    lines = path.read_bytes().splitlines(keepends=True)
    if end_line > len(lines):
        raise EvidenceMiddlewareError("line range exceeds file length")
    return b"".join(lines[start_line - 1 : end_line])


def _assert_evidence_safe(evidence: EvidenceRef) -> None:
    if isinstance(evidence, FileEvidenceRef):
        _assert_safe_file_path(evidence.path)
    if isinstance(evidence, WebEvidenceRef):
        _assert_url_has_no_credentials(evidence.url)


def _assert_safe_file_path(path: str) -> None:
    lowered_parts = [part.lower() for part in Path(path).parts]
    if ".env" in lowered_parts:
        raise EvidenceMiddlewareError(".env paths must not enter Evidence Index")
    forbidden_names = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
    if forbidden_names.intersection(lowered_parts):
        raise EvidenceMiddlewareError("private key paths must not enter Evidence Index")


def _assert_url_has_no_credentials(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        raise EvidenceMiddlewareError("credential-bearing URLs must not enter Evidence Index")
