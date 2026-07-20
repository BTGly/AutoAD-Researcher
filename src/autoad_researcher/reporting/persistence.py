"""Immutable report artifacts written through the report store's run-level lock."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoad_researcher.reporting.snapshot import canonical_sha256, sha256_file, utc_now
from autoad_researcher.reporting.store import MANIFEST_FILE, ReportStore
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2


def write_immutable_report_json(
    run_dir: Path,
    *,
    report_id: str,
    filename: str,
    artifact_type: str,
    value: Any,
    content_sha256: str | None = None,
) -> ArtifactReferenceV2:
    """Write one canonical report artifact once and attach a SHA-bearing ref."""

    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError("report artifact filename must be a single path component")
    store = ReportStore()
    with store._lock(run_dir):
        directory = store._report_dir(run_dir, report_id)
        path = directory / filename
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if path.is_file():
            if path.read_text(encoding="utf-8") != payload:
                raise ValueError("immutable report artifact already exists with different content")
        else:
            store._write_json_unlocked(path, value)
        reference = ArtifactReferenceV2(
            artifact_id=f"report_artifact:{report_id}:{filename}",
            artifact_type=artifact_type,
            locator=str(path.relative_to(run_dir)),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
        )
        manifest = store.load_manifest(run_dir, report_id)
        existing = {item.artifact_id: item for item in manifest.artifact_refs}
        previous = existing.get(reference.artifact_id)
        if previous is not None and previous != reference:
            raise ValueError("report manifest already binds this artifact ID differently")
        existing[reference.artifact_id] = reference
        updates: dict[str, Any] = {
            "artifact_refs": [existing[key] for key in sorted(existing)],
            "updated_at": utc_now(),
            "revision": manifest.revision + 1,
        }
        if filename == "report_facts.json":
            updates["facts_content_sha256"] = content_sha256 or canonical_sha256(value)
        store._write_json_unlocked(directory / MANIFEST_FILE, manifest.model_copy(update=updates).model_dump(mode="json"))
        return reference
