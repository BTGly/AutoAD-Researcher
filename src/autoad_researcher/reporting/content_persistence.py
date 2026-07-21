"""Immutable text report artifacts registered in the report manifest."""

from autoad_researcher.reporting.binary_persistence import write_immutable_report_bytes
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2


def write_immutable_report_text(run_dir, *, report_id: str, filename: str, artifact_type: str, text: str) -> ArtifactReferenceV2:
    return write_immutable_report_bytes(
        run_dir,
        report_id=report_id,
        filename=filename,
        artifact_type=artifact_type,
        content=text.encode("utf-8"),
    )
