"""Immutable text artifacts for report content publication."""

import hashlib
import os
from pathlib import Path

from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2


def write_immutable_report_text(run_dir: Path, *, report_id: str, filename: str, artifact_type: str, text: str) -> ArtifactReferenceV2:
    if not filename or "/" in filename or "\\" in filename:
        raise ValueError("report text filename must be a single path component")
    store = ReportStore()
    with store._lock(run_dir):
        directory = store._report_dir(run_dir, report_id)
        path = directory / filename
        if path.is_file():
            if path.read_text(encoding="utf-8") != text:
                raise ValueError("immutable report text already exists with different content")
        else:
            temporary = path.with_suffix(path.suffix + ".tmp")
            try:
                with temporary.open("w", encoding="utf-8") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return ArtifactReferenceV2(artifact_id=f"report_artifact:{report_id}:{filename}", artifact_type=artifact_type, locator=str(path.relative_to(run_dir)), sha256=digest, size_bytes=path.stat().st_size)
