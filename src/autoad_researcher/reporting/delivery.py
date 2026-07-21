"""Explicit download metadata for the finite report-artifact allow-list."""

from pathlib import Path

from autoad_researcher.reporting.models import ReportArtifactDelivery
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2

_DELIVERY_METADATA: dict[str, tuple[str, str]] = {
    "report_markdown": ("text/markdown", "attachment"),
    "report_html": ("text/html", "inline"),
    "report_pdf": ("application/pdf", "inline"),
    "report_bundle": ("application/zip", "attachment"),
    "report_checksums": ("text/plain", "attachment"),
    "report_facts": ("application/json", "attachment"),
    "report_evidence_index": ("application/json", "attachment"),
    "report_digest": ("application/json", "attachment"),
    "report_narrative": ("application/json", "attachment"),
    "report_validation": ("application/json", "attachment"),
    "report_claim_evidence_map": ("application/json", "attachment"),
    "report_pdf_result": ("application/json", "attachment"),
    "report_delivery_snapshot": ("application/json", "attachment"),
    "report_bundle_exclusions": ("application/json", "attachment"),
}


def build_delivery(report_id: str, reference: ArtifactReferenceV2) -> ReportArtifactDelivery:
    try:
        media_type, disposition = _DELIVERY_METADATA[reference.artifact_type]
    except KeyError as exc:
        raise ValueError("report artifact type has no delivery metadata") from exc
    return ReportArtifactDelivery(
        artifact_ref=reference,
        media_type=media_type,
        download_filename=f"{report_id}-{Path(reference.locator).name}",
        content_disposition_type=disposition,
    )
