"""Typed contracts for immutable report inputs and mutable report state."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.schemas.artifacts import ArtifactReferenceV2

GenerationStatus = Literal[
    "queued",
    "building_snapshot",
    "assembling_facts",
    "generating_narrative",
    "validating",
    "content_ready",
    "failed",
]
ReviewStatus = Literal["unreviewed", "accepted", "needs_more", "needs_repair", "disputed"]
FormatState = Literal["missing", "queued", "ready", "failed"]


class ReportFormatStatus(BaseModel):
    """Per-artifact availability; none of these decides scientific review."""

    model_config = ConfigDict(extra="forbid")

    markdown: FormatState = "missing"
    html: FormatState = "missing"
    pdf: FormatState = "missing"
    bundle: FormatState = "missing"


class ReportSnapshot(BaseModel):
    """Frozen inventory of report inputs, not a duplicate of all experiment data."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    source_refs: list[ArtifactReferenceV2]
    frozen_control_plane: dict[str, list[dict]] = Field(default_factory=dict)
    session_revision: int = Field(ge=0)
    evaluation_contract_ref: str | None = None
    environment_snapshot_ref: str | None = None
    source_inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_at: str


class ReportManifest(BaseModel):
    """Immutable identity and lineage for one report version."""

    # Earlier report files carried mutable fields. Ignore those only on read;
    # all new manifests are emitted from this immutable contract.
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    report_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    source_snapshot_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_policy_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    report_recipe_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_at: str
    previous_report_id: str | None = None
    parent_report_id: str | None = None
    source_proposal_id: str | None = None
    model_route: dict[str, Any] = Field(default_factory=dict)


class ReportArtifactDelivery(BaseModel):
    """Download metadata bound to one SHA-bearing report artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_ref: ArtifactReferenceV2
    media_type: str = Field(min_length=1)
    download_filename: str = Field(min_length=1)
    content_disposition_type: Literal["attachment", "inline"]


class ReportState(BaseModel):
    """Mutable state kept beside the manifest to make recovery explicit."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    report_id: str = Field(min_length=1)
    generation_status: GenerationStatus = "queued"
    review_status: ReviewStatus = "unreviewed"
    format_status: ReportFormatStatus = Field(default_factory=ReportFormatStatus)
    facts_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    artifact_refs: list[ArtifactReferenceV2] = Field(default_factory=list)
    deliveries: list[ReportArtifactDelivery] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    last_error: str | None = None
    updated_at: str
    revision: int = Field(default=0, ge=0)
