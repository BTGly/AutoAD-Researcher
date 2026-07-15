"""Deterministic Evidence Validator for Repository Intelligence R9."""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.repository_intelligence.evidence import read_evidence_index
from autoad_researcher.repository_intelligence.evidence_models import (
    FileEvidenceRef,
    RepositoryIdentityEvidenceRef,
    WebEvidenceRef,
)
from autoad_researcher.repository_intelligence.ids import IdentifierPattern, validate_relative_path
from autoad_researcher.repository_intelligence.models import RepositoryArtifactPaths, RepositorySource


class ValidationIssue(BaseModel):
    """One deterministic validation issue."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(pattern=IdentifierPattern)
    severity: Literal["error", "warning"]
    location: str
    message: str = Field(min_length=1)


class RepositoryValidationReport(BaseModel):
    """Repository Intelligence validation report."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    status: Literal["passed", "failed"]
    checked_evidence_count: int = Field(ge=0)
    checked_artifact_count: int = Field(ge=0)
    issues: list[ValidationIssue] = Field(default_factory=list)


def validate_repository_intelligence_run(
    *,
    source: RepositorySource,
    repository_root: Path,
    run_dir: Path,
    artifacts: RepositoryArtifactPaths,
    supplemental_artifacts: list[str] | None = None,
) -> RepositoryValidationReport:
    """Validate evidence refs and synthesized artifacts for one run."""
    issues: list[ValidationIssue] = []
    evidence_records = read_evidence_index(run_dir / "evidence_index.jsonl")
    evidence_ids: set[str] = set()
    for record in evidence_records:
        evidence = record.evidence
        evidence_ids.add(evidence.evidence_id)
        if isinstance(evidence, FileEvidenceRef):
            issues.extend(_validate_file_evidence(evidence, source=source, repository_root=repository_root))
        elif isinstance(evidence, RepositoryIdentityEvidenceRef):
            issues.extend(_validate_identity_evidence(evidence, source=source))
        elif isinstance(evidence, WebEvidenceRef):
            issues.extend(_validate_web_evidence(evidence))

    checked_artifact_count = 0
    for artifact_path in artifacts.path_set():
        path = run_dir / artifact_path
        if not path.is_file():
            issues.append(_issue("ARTIFACT_MISSING", "error", artifact_path, "formal artifact file is missing"))
            continue
        checked_artifact_count += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append(_issue("ARTIFACT_JSON_INVALID", "error", artifact_path, str(exc)))
            continue
        issues.extend(_validate_artifact_claims(payload, artifact_path, evidence_ids))

    for artifact_path in supplemental_artifacts or []:
        path = run_dir / validate_relative_path(artifact_path)
        if not path.is_file():
            issues.append(_issue("ARTIFACT_MISSING", "error", artifact_path, "supplemental artifact file is missing"))
            continue
        checked_artifact_count += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append(_issue("ARTIFACT_JSON_INVALID", "error", artifact_path, str(exc)))
            continue
        issues.extend(_validate_supplemental_evidence_refs(payload, artifact_path, evidence_ids))

    status = "failed" if any(issue.severity == "error" for issue in issues) else "passed"
    return RepositoryValidationReport(
        schema_version=1,
        status=status,
        checked_evidence_count=len(evidence_records),
        checked_artifact_count=checked_artifact_count,
        issues=issues,
    )


def _validate_file_evidence(
    evidence: FileEvidenceRef,
    *,
    source: RepositorySource,
    repository_root: Path,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if evidence.source_id != source.source_id:
        issues.append(_issue("EVIDENCE_SOURCE_MISMATCH", "error", evidence.evidence_id, "file evidence source_id does not match source"))
    if source.resolved_commit is not None and evidence.repository_commit != source.resolved_commit:
        issues.append(_issue("EVIDENCE_COMMIT_MISMATCH", "error", evidence.evidence_id, "file evidence commit does not match source"))
    path = repository_root / evidence.path
    if not path.is_file():
        issues.append(_issue("EVIDENCE_FILE_MISSING", "error", evidence.evidence_id, "file evidence path does not exist"))
        return issues
    actual_file_sha = sha256_file(path)
    if actual_file_sha != evidence.file_sha256:
        issues.append(_issue("EVIDENCE_FILE_SHA_MISMATCH", "error", evidence.evidence_id, "file SHA256 does not match"))
    lines = path.read_bytes().splitlines(keepends=True)
    if evidence.end_line > len(lines) or evidence.start_line > evidence.end_line:
        issues.append(_issue("EVIDENCE_LINE_RANGE_INVALID", "error", evidence.evidence_id, "file evidence line range is invalid"))
        return issues
    snippet = b"".join(lines[evidence.start_line - 1 : evidence.end_line])
    actual_snippet_sha = hashlib.sha256(snippet).hexdigest()
    if actual_snippet_sha != evidence.snippet_sha256:
        issues.append(_issue("EVIDENCE_SNIPPET_SHA_MISMATCH", "error", evidence.evidence_id, "snippet SHA256 does not match"))
    return issues


def _validate_identity_evidence(evidence: RepositoryIdentityEvidenceRef, *, source: RepositorySource) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if evidence.source_id != source.source_id:
        issues.append(_issue("IDENTITY_SOURCE_MISMATCH", "error", evidence.evidence_id, "identity source_id does not match source"))
    if evidence.resolved_commit != source.resolved_commit:
        issues.append(_issue("IDENTITY_COMMIT_MISMATCH", "error", evidence.evidence_id, "identity commit does not match source"))
    if evidence.tree_sha != source.tree_sha:
        issues.append(_issue("IDENTITY_TREE_MISMATCH", "error", evidence.evidence_id, "identity tree_sha does not match source"))
    if evidence.dirty != source.dirty:
        issues.append(_issue("IDENTITY_DIRTY_MISMATCH", "error", evidence.evidence_id, "identity dirty flag does not match source"))
    return issues


def _validate_web_evidence(evidence: WebEvidenceRef) -> list[ValidationIssue]:
    parsed = urlsplit(evidence.url)
    if parsed.username or parsed.password:
        return [_issue("WEB_EVIDENCE_CREDENTIAL_URL", "error", evidence.evidence_id, "web evidence URL contains credentials")]
    return []


def _validate_artifact_claims(payload: Any, location: str, evidence_ids: set[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if isinstance(payload, dict):
        if {"claim_id", "status", "summary"}.issubset(payload):
            claim_id = str(payload["claim_id"])
            status = payload["status"]
            claim_evidence = payload.get("evidence_ids", [])
            if status == "confirmed" and not claim_evidence:
                issues.append(_issue("CLAIM_CONFIRMED_WITHOUT_EVIDENCE", "error", f"{location}:{claim_id}", "confirmed claim has no evidence_ids"))
            if status == "inferred" and not payload.get("rationale_summary"):
                issues.append(_issue("CLAIM_INFERRED_WITHOUT_RATIONALE", "error", f"{location}:{claim_id}", "inferred claim has no rationale_summary"))
            for evidence_id in claim_evidence:
                if evidence_id not in evidence_ids:
                    issues.append(_issue("CLAIM_EVIDENCE_MISSING", "error", f"{location}:{claim_id}", f"claim references missing evidence_id: {evidence_id}"))
        for key, value in payload.items():
            issues.extend(_validate_artifact_claims(value, f"{location}.{key}", evidence_ids))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            issues.extend(_validate_artifact_claims(value, f"{location}[{index}]", evidence_ids))
    return issues


def _validate_supplemental_evidence_refs(
    payload: Any,
    location: str,
    evidence_ids: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if isinstance(payload, dict):
        evidence_id = payload.get("evidence_id")
        if isinstance(evidence_id, str) and evidence_id not in evidence_ids:
            issues.append(_issue(
                "ARTIFACT_EVIDENCE_MISSING",
                "error",
                location,
                f"supplemental artifact references missing evidence_id: {evidence_id}",
            ))
        referenced_ids = payload.get("evidence_ids")
        if isinstance(referenced_ids, list):
            for referenced_id in referenced_ids:
                if isinstance(referenced_id, str) and referenced_id not in evidence_ids:
                    issues.append(_issue(
                        "ARTIFACT_EVIDENCE_MISSING",
                        "error",
                        location,
                        f"supplemental artifact references missing evidence_id: {referenced_id}",
                    ))
        for key, value in payload.items():
            issues.extend(_validate_supplemental_evidence_refs(value, f"{location}.{key}", evidence_ids))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            issues.extend(_validate_supplemental_evidence_refs(value, f"{location}[{index}]", evidence_ids))
    return issues


def _issue(code: str, severity: Literal["error", "warning"], location: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, location=location, message=message)
