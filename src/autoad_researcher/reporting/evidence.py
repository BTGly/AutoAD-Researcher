"""Evidence entries derived only from the SHA-verified report snapshot."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.reporting.snapshot import attempt_id_from_artifact, canonical_sha256, read_verified_snapshot_artifact, resolve_run_relative_file, sha256_file
from autoad_researcher.reporting.models import ReportSnapshot
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2

EVIDENCE_INDEX_BUILD_VERSION = "v2"


class EvidenceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    evidence_kind: str = Field(min_length=1)
    artifact_ref: ArtifactReferenceV2
    source_object_id: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    # Fact paths are an explicit projection, not inferred from Narrative text.
    fact_refs: list[str] = Field(default_factory=list)
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
    facts=None,
) -> EvidenceIndex:
    """Create stable root and leaf-field evidence from verified snapshot values."""

    entries: list[EvidenceEntry] = []
    snapshot_path = run_dir / "reports" / report_id / "report_snapshot.json"
    snapshot_ref = ArtifactReferenceV2(
        artifact_id=f"report_snapshot:{report_id}",
        artifact_type="report_snapshot",
        locator=str(snapshot_path.relative_to(run_dir)),
        sha256=sha256_file(snapshot_path),
        size_bytes=snapshot_path.stat().st_size,
    )
    frozen_types = set(snapshot.frozen_control_plane)
    for reference in snapshot.source_refs:
        if reference.artifact_type in frozen_types:
            evidence_reference = snapshot_ref.model_copy(
                update={
                    "artifact_id": f"{reference.artifact_id}:frozen",
                    "artifact_type": f"frozen_{reference.artifact_type}",
                }
            )
        else:
            evidence_reference = reference
        value = _snapshot_value(run_dir, snapshot, reference)
        for field_path in ("$", *_leaf_paths(value)):
            identity = {"source_object_id": reference.artifact_id, "artifact_id": reference.artifact_id, "field_path": field_path}
            entries.append(EvidenceEntry(
                evidence_id=f"evidence_{canonical_sha256(identity)[:24]}",
                evidence_kind=evidence_reference.artifact_type,
                artifact_ref=evidence_reference,
                source_object_id=reference.artifact_id,
                field_path=field_path,
                fact_refs=_fact_refs_for_source(
                    facts, reference.artifact_type, reference.artifact_id, field_path
                ),
                attempt_id=_attempt_id(reference),
                idea_id=_idea_id(reference),
                summary=f"Verified {reference.artifact_type} field {field_path}",
            ))
    return EvidenceIndex(
        report_id=report_id,
        snapshot_content_sha256=snapshot_content_sha256,
        entries=entries,
    )


def _fact_refs_for_source(facts, artifact_type: str, artifact_id: str, field_path: str) -> list[str]:
    """Return the report-Facts paths directly projected from one source field."""

    if facts is None:
        return []
    if artifact_type == "experiment_session":
        return _session_fact_refs(field_path)
    if artifact_type == "candidate_snapshot":
        return _candidate_fact_refs(facts, artifact_id, field_path)
    if artifact_type == "idea_tree":
        return _idea_fact_refs(field_path)
    if artifact_type == "environment_snapshot":
        if field_path == "environment_path":
            return []
        prefix = "repository_and_environment.environment_snapshot"
        return [prefix if field_path == "$" else f"{prefix}.snapshot.{field_path}"]
    attempt_id = _id_part(artifact_id, "attempt_")
    if attempt_id is not None:
        index = next((i for i, item in enumerate(facts.attempts) if item.get("attempt_id") == attempt_id), None)
        if index is None:
            return []
        prefixes = {
            "experiment_attempt": f"attempts.{index}",
            "outcome_card": f"attempts.{index}.outcome",
            "scientific_assessment": f"attempts.{index}.assessment",
            "assessment_reconciliation": f"attempts.{index}.assessment_reconciliation",
            "scientific_evaluation_inputs": f"attempts.{index}.scientific_evaluation_inputs",
            "attempt_metrics": f"attempts.{index}.attempt_metrics",
            "failure_classification": f"attempts.{index}.failure_classification",
            "execution_result": f"attempts.{index}.execution_result",
            "resource_usage_report": f"attempts.{index}.resource_usage_report",
        }
        prefix = prefixes.get(artifact_type)
        if prefix is not None:
            refs = [prefix if field_path == "$" else f"{prefix}.{field_path}"]
            refs.extend(_attempt_projection_refs(facts, attempt_id, artifact_type, field_path))
            return sorted(set(refs))
    direct = {
        "evaluation_contract": "evaluation_contract",
        "cognitive_cost_summary": "cognitive_cost_summary",
        "stop_decision": "stop_decision",
        "champion_pointers": "candidate_and_champion.current_by_contract",
        "idea_tree": "ideas",
    }.get(artifact_type)
    if direct is None:
        return []
    return [direct if field_path == "$" else f"{direct}.{field_path}"]


def _session_fact_refs(field_path: str) -> list[str]:
    direct = {
        "task_ref": "research_objective.task_ref",
        "status": "repository_and_environment.status",
        "repository_ref": "repository_and_environment.repository_ref",
        "session_id": "session_id",
    }.get(field_path)
    return [direct] if direct else []


def _candidate_fact_refs(facts, artifact_id: str, field_path: str) -> list[str]:
    prefix = "candidate_snapshot:"
    candidate_id = artifact_id.removeprefix(prefix) if artifact_id.startswith(prefix) else ""
    index = next(
        (i for i, item in enumerate(facts.candidate_and_champion.get("candidates", [])) if item.get("candidate_id") == candidate_id),
        None,
    )
    if index is None:
        return []
    prefix = f"candidate_and_champion.candidates.{index}"
    return [prefix if field_path == "$" else f"{prefix}.{field_path}"]


def _idea_fact_refs(field_path: str) -> list[str]:
    if field_path == "$":
        return ["ideas"]
    parts = field_path.split(".")
    if len(parts) >= 2 and parts[0] == "nodes" and parts[1].isdigit():
        return ["ideas." + ".".join(parts[1:])]
    return []


def _attempt_projection_refs(facts, attempt_id: str, artifact_type: str, field_path: str) -> list[str]:
    """Map deterministic list projections back to the authoritative Attempt field."""

    suffix = "" if field_path == "$" else f".{field_path}"
    refs: list[str] = []
    for name in ("baseline", "failed_attempts", "non_comparable_attempts"):
        values = getattr(facts, name)
        derived_index = next((i for i, item in enumerate(values) if item.get("attempt_id") == attempt_id), None)
        if derived_index is None:
            continue
        if artifact_type == "experiment_attempt":
            refs.append(f"{name}.{derived_index}{suffix}")
        elif artifact_type == "outcome_card":
            refs.append(f"{name}.{derived_index}.outcome{suffix}")
        elif artifact_type == "scientific_assessment":
            refs.append(f"{name}.{derived_index}.assessment{suffix}")
    if artifact_type == "scientific_assessment":
        validity_index = next((i for i, item in enumerate(facts.validity) if item.get("attempt_id") == attempt_id), None)
        if validity_index is not None:
            refs.append(f"validity.{validity_index}{suffix}")
    if artifact_type == "outcome_card" and field_path.startswith("metrics."):
        metric = field_path.split(".", 1)[1]
        for name in ("primary_metrics", "guardrail_metrics"):
            metric_index = next(
                (i for i, item in enumerate(getattr(facts, name)) if item.get("attempt_id") == attempt_id and item.get("metric") == metric),
                None,
            )
            if metric_index is not None:
                refs.append(f"{name}.{metric_index}.value")
    return refs


def _id_part(artifact_id: str, prefix: str) -> str | None:
    return next((part for part in artifact_id.split(":") if part.startswith(prefix)), None)


def _attempt_id(reference: ArtifactReferenceV2) -> str | None:
    return attempt_id_from_artifact(reference)


def _idea_id(reference: ArtifactReferenceV2) -> str | None:
    parts = reference.artifact_id.split(":")
    return next((part for part in parts if part.startswith("idea_")), None)


def _snapshot_value(run_dir, snapshot: ReportSnapshot, reference: ArtifactReferenceV2) -> dict[str, Any]:
    frozen = snapshot.frozen_control_plane.get(reference.artifact_type)
    if frozen is not None:
        return next(
            (item for item in frozen if _object_id(item) in reference.artifact_id),
            {},
        )
    return read_verified_snapshot_artifact(run_dir, reference)


def _object_id(value: dict[str, Any]) -> str:
    for key in ("attempt_id", "session_id", "candidate_id"):
        item = value.get(key)
        if isinstance(item, str):
            return item
    return ""


def _leaf_paths(value: object, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else key
            result.extend(_leaf_paths(value[key], path))
        return result or ([prefix] if prefix else [])
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            path = f"{prefix}.{index}" if prefix else str(index)
            result.extend(_leaf_paths(item, path))
        return result or ([prefix] if prefix else [])
    return [prefix] if prefix else []
