"""Build and verify the frozen, SHA-bound inventory for a report."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.models import ReportSnapshot
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2

_FROZEN_CONTROL_PLANE_TYPES = frozenset(
    {
        "experiment_session",
        "experiment_attempt",
        "idea_tree",
        "cognitive_cost_summary",
        "stop_decision",
        "candidate_snapshot",
        "champion_pointers",
    }
)
SNAPSHOT_POLICY_VERSION = "v1"
SNAPSHOT_FREEZE_ATTEMPTS = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any, *, volatile_keys: frozenset[str] = frozenset()) -> str:
    """Hash canonical JSON while explicitly excluding only named volatile fields."""

    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items() if key not in volatile_keys}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        return item

    payload = json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_run_relative_file(run_dir: Path, locator: str) -> Path:
    """Resolve one registered locator without allowing traversal or symlink escape."""

    candidate = PurePosixPath(locator)
    if not locator or candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("artifact locator must be a non-empty run-relative path")
    root = run_dir.resolve()
    path = run_dir.joinpath(*candidate.parts)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("artifact locator escapes run directory") from exc
    if not resolved.is_file():
        raise ValueError("artifact locator must resolve to a file")
    return resolved


def build_report_snapshot(run_dir: Path, *, session_id: str) -> ReportSnapshot:
    """Freeze control-plane copies after a bounded optimistic stability check."""

    for _ in range(SNAPSHOT_FREEZE_ATTEMPTS):
        session, source_refs = _session_and_sources(run_dir, session_id)
        frozen_control_plane = _freeze_control_plane(run_dir, source_refs)
        current, current_refs = _session_and_sources(run_dir, session_id)
        if session.revision != current.revision or source_refs != current_refs:
            continue
        inventory_hash = canonical_sha256([item.model_dump(mode="json") for item in source_refs])
        return ReportSnapshot(
            run_id=run_dir.name,
            session_id=session.session_id,
            source_refs=source_refs,
            frozen_control_plane=frozen_control_plane,
            session_revision=session.revision,
            evaluation_contract_ref=session.evaluation_contract_ref,
            environment_snapshot_ref=session.environment_snapshot_ref,
            source_inventory_sha256=inventory_hash,
            frozen_at=utc_now(),
        )
    raise ValueError("report sources changed while the Snapshot was being frozen")


def snapshot_content_sha256(snapshot: ReportSnapshot) -> str:
    return canonical_sha256(snapshot.model_dump(mode="json"), volatile_keys=frozenset({"frozen_at"}))


def _freeze_control_plane(run_dir: Path, references: list[ArtifactReferenceV2]) -> dict[str, list[dict]]:
    """Copy only small mutable control-plane JSON; artifacts remain SHA-bound refs."""

    frozen: dict[str, list[dict]] = {}
    for reference in references:
        if reference.artifact_type not in _FROZEN_CONTROL_PLANE_TYPES:
            continue
        path = resolve_run_relative_file(run_dir, reference.locator)
        if sha256_file(path) != reference.sha256:
            raise ValueError("report source changed while freezing Snapshot")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("report control-plane source is not readable JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("report control-plane source must be a JSON object")
        frozen.setdefault(reference.artifact_type, []).append(value)
    return {key: frozen[key] for key in sorted(frozen)}


def snapshot_policy_hash() -> str:
    return canonical_sha256({"policy_version": SNAPSHOT_POLICY_VERSION, "frozen_types": sorted(_FROZEN_CONTROL_PLANE_TYPES)})


def _session_and_sources(run_dir: Path, session_id: str):
    session = ExperimentSessionStore().load(run_dir, session_id)
    if session is None:
        raise FileNotFoundError("experiment session not found")
    if session.run_id != run_dir.name:
        raise ValueError("Session does not belong to run directory")
    session_locator = f"experiments/sessions/{session.session_id}.json"
    session_path = resolve_run_relative_file(run_dir, session_locator)
    session_ref = ArtifactReferenceV2(
        artifact_id=f"experiment_session:{session.session_id}",
        artifact_type="experiment_session",
        locator=session_locator,
        sha256=sha256_file(session_path),
        size_bytes=session_path.stat().st_size,
    )
    from autoad_researcher.reporting.inventory import collect_snapshot_sources

    return session, collect_snapshot_sources(run_dir, session_id=session.session_id, session=session, session_ref=session_ref)
