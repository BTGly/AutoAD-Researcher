"""Evidence entries derived only from the SHA-verified report snapshot."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.reporting.snapshot import canonical_sha256, resolve_run_relative_file, sha256_file
from autoad_researcher.reporting.models import ReportSnapshot
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2


class EvidenceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    evidence_kind: str = Field(min_length=1)
    artifact_ref: ArtifactReferenceV2
    source_object_id: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    attempt_id: str | None = None
    idea_id: str | None = None
    summary: str


class EvidenceIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    report_id: str = Field(min_length=1)
    snapshot_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: list[EvidenceEntry]


def build_evidence_index(
    run_dir,
    *,
    report_id: str,
    snapshot_content_sha256: str,
    snapshot: ReportSnapshot,
) -> EvidenceIndex:
    """Create stable root-level evidence entries after verifying each snapshot ref."""

    entries: list[EvidenceEntry] = []
    for reference in snapshot.source_refs:
        resolved = resolve_run_relative_file(run_dir, reference.locator)
        if sha256_file(resolved) != reference.sha256:
            raise ValueError("snapshot artifact SHA-256 no longer matches")
        identity = {
            "source_object_id": reference.artifact_id,
            "artifact_id": reference.artifact_id,
            "field_path": "$",
        }
        entries.append(
            EvidenceEntry(
                evidence_id=f"evidence_{canonical_sha256(identity)[:24]}",
                evidence_kind=reference.artifact_type,
                artifact_ref=reference,
                source_object_id=reference.artifact_id,
                field_path="$",
                attempt_id=_attempt_id(reference),
                idea_id=_idea_id(reference),
                summary=f"Verified {reference.artifact_type} artifact",
            )
        )
    return EvidenceIndex(
        report_id=report_id,
        snapshot_content_sha256=snapshot_content_sha256,
        entries=entries,
    )


def _attempt_id(reference: ArtifactReferenceV2) -> str | None:
    parts = reference.artifact_id.split(":")
    return next((part for part in parts if part.startswith("attempt_")), None)


def _idea_id(reference: ArtifactReferenceV2) -> str | None:
    parts = reference.artifact_id.split(":")
    return next((part for part in parts if part.startswith("idea_")), None)
