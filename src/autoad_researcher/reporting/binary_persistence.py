"""Immutable byte artifacts attached to the report manifest under its lock."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from autoad_researcher.reporting.snapshot import utc_now
from autoad_researcher.reporting.delivery import build_delivery
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2


def write_immutable_report_bytes(
    run_dir: Path,
    *,
    report_id: str,
    filename: str,
    artifact_type: str,
    content: bytes,
) -> ArtifactReferenceV2:
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError("report artifact filename must be a single path component")
    store = ReportStore()
    with store._lock(run_dir):
        directory = store._report_dir(run_dir, report_id)
        path = directory / filename
        if path.is_file():
            if path.read_bytes() != content:
                raise ValueError("immutable report artifact already exists with different content")
        else:
            temporary = path.with_suffix(path.suffix + ".tmp")
            try:
                with temporary.open("wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        reference = ArtifactReferenceV2(
            artifact_id=f"report_artifact:{report_id}:{filename}",
            artifact_type=artifact_type,
            locator=str(path.relative_to(run_dir)),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size_bytes=path.stat().st_size,
        )
        state = store.load_state(run_dir, report_id)
        existing = {item.artifact_id: item for item in state.artifact_refs}
        previous = existing.get(reference.artifact_id)
        if previous is not None and previous != reference:
            raise ValueError("report State already binds this artifact ID differently")
        existing[reference.artifact_id] = reference
        delivery = build_delivery(report_id, reference)
        deliveries = {item.artifact_ref.artifact_id: item for item in state.deliveries}
        previous_delivery = deliveries.get(reference.artifact_id)
        if previous_delivery is not None and previous_delivery != delivery:
            raise ValueError("report State already binds this artifact delivery differently")
        deliveries[reference.artifact_id] = delivery
        updated = state.model_copy(update={
            "artifact_refs": [existing[key] for key in sorted(existing)],
            "deliveries": [deliveries[key] for key in sorted(deliveries)],
            "updated_at": utc_now(),
            "revision": state.revision + 1,
        })
        store._write_json_unlocked(directory / "report_state.json", updated.model_dump(mode="json"))
        return reference
