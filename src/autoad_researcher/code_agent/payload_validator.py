"""DeterministicPatchPayloadValidator — validates PatchPayload integrity via ArtifactStore."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from autoad_researcher.core.artifacts import ArtifactStore
from autoad_researcher.schemas.patch_planning import (
    PatchPayload,
    PatchPayloadManifest,
    PatchPayloadValidationIssue,
    PatchPayloadValidationReport,
    PlannedRepositoryChange,
    RepositoryChangePlan,
    canonical_sha,
)


def validate_payload_manifest(
    *,
    manifest: PatchPayloadManifest,
    plan: RepositoryChangePlan,
    repository_root: Path,
    report_id: str,
    artifact_store: ArtifactStore,
) -> PatchPayloadValidationReport:
    """Deterministic validation of all payloads in a PatchPayloadManifest.

    Checks:
      1. payload_sha256 matches actual artifact content (from ArtifactStore if available)
      2. before_sha256 matches current file content
      3. target_before_sha256 matches target file content (replace_existing)
      4. Each change_id exists in plan
      5. target_path matches plan change's repository_path
      6. canonical_sha(manifest) == manifest.manifest_sha256
      7. manifest.run_id == plan.run_id and manifest.patch_plan_sha256 == plan.patch_plan_sha256
      8. Payload ID matches change.payload_id
      9. Missing / duplicate / extra payloads vs plan changes
      10. before_sha256 required for modify/rename (fail if missing)
      11. target_before_sha256 required for replace_existing (fail if missing)
      12. For rename: before_sha256 → source file, target_before_sha256 → target file
    """
    issues: list[PatchPayloadValidationIssue] = []
    plan_changes = {c.change_id: c for c in plan.changes}
    plan_payload_ids = {c.change_id: c.payload_id for c in plan.changes if c.payload_id}

    # 6. canonical_sha(manifest)
    computed_manifest_sha = canonical_sha(manifest)
    if computed_manifest_sha != manifest.manifest_sha256:
        issues.append(PatchPayloadValidationIssue(
            issue_id="ppvi_manifest_sha",
            category="payload_sha_mismatch",
            description=f"canonical_sha(manifest)={computed_manifest_sha[:16]} != manifest.manifest_sha256",
            resolution="regenerate",
        ))

    # 7. manifest identity binding
    if manifest.run_id != plan.run_id:
        issues.append(PatchPayloadValidationIssue(
            issue_id="ppvi_manifest_run",
            category="undeclared_path",
            description=f"manifest.run_id={manifest.run_id} != plan.run_id={plan.run_id}",
            resolution="blocked",
        ))
    if manifest.patch_plan_sha256 != plan.patch_plan_sha256:
        issues.append(PatchPayloadValidationIssue(
            issue_id="ppvi_manifest_plan_sha",
            category="undeclared_path",
            description="manifest.patch_plan_sha256 != plan.patch_plan_sha256",
            resolution="blocked",
        ))

    # 9. Payload coverage + duplicate detection
    manifest_payload_cids = {p.change_id for p in manifest.payloads}
    manifest_payload_ids = {p.payload_id for p in manifest.payloads}
    if len(manifest.payloads) != len(manifest_payload_cids):
        issues.append(PatchPayloadValidationIssue(
            issue_id="ppvi_duplicate_cid",
            category="undeclared_file_creation",
            description="duplicate change_id in manifest payloads",
            resolution="blocked",
        ))
    if len(manifest.payloads) != len(manifest_payload_ids):
        issues.append(PatchPayloadValidationIssue(
            issue_id="ppvi_duplicate_pid",
            category="undeclared_file_creation",
            description="duplicate payload_id in manifest payloads",
            resolution="blocked",
        ))
    plan_needs_payload = {cid for cid, pid in plan_payload_ids.items() if pid is not None}
    extra_cids = manifest_payload_cids - plan_needs_payload
    missing_cids = plan_needs_payload - manifest_payload_cids

    for cid in extra_cids:
        issues.append(PatchPayloadValidationIssue(
            issue_id=f"ppvi_extra_{cid}",
            category="undeclared_file_creation",
            description=f"payload for change_id {cid} not in plan",
            change_id=cid,
            resolution="blocked",
        ))
    for cid in missing_cids:
        issues.append(PatchPayloadValidationIssue(
            issue_id=f"ppvi_missing_{cid}",
            category="undeclared_path",
            description=f"change_id {cid} in plan has payload_id but no payload in manifest",
            change_id=cid,
            resolution="regenerate",
        ))

    # Per-payload validation
    for payload in manifest.payloads:
        issues.extend(_validate_single_payload(
            payload, plan_changes, plan_payload_ids, repository_root,
            artifact_store, manifest.run_id,
        ))

    status = "passed" if not issues else "failed"
    return PatchPayloadValidationReport(
        report_id=report_id,
        patch_plan_sha256=plan.patch_plan_sha256,
        payload_manifest_sha256=manifest.manifest_sha256,
        status=status,
        issues=issues,
        validated_at=datetime.now(timezone.utc),
    )


def _validate_single_payload(
    payload: PatchPayload,
    plan_changes: dict[str, PlannedRepositoryChange],
    plan_payload_ids: dict[str, Optional[str]],
    repository_root: Path,
    artifact_store: ArtifactStore,
    run_id: str,
) -> list[PatchPayloadValidationIssue]:
    issues: list[PatchPayloadValidationIssue] = []

    change = plan_changes.get(payload.change_id)
    if change is None:
        issues.append(PatchPayloadValidationIssue(
            issue_id=f"ppvi_{payload.payload_id}_no_change",
            category="undeclared_path",
            description=f"change_id {payload.change_id} not in plan",
            payload_id=payload.payload_id,
            change_id=payload.change_id,
            resolution="blocked",
        ))
        return issues

    if payload.target_path != change.repository_path:
        issues.append(PatchPayloadValidationIssue(
            issue_id=f"ppvi_{payload.payload_id}_path",
            category="undeclared_path",
            description=f"payload target_path {payload.target_path} != plan path {change.repository_path}",
            payload_id=payload.payload_id,
            change_id=payload.change_id,
            resolution="blocked",
        ))

    # 8. Payload ID matches change
    expected_pid = plan_payload_ids.get(payload.change_id)
    if expected_pid and payload.payload_id != expected_pid:
        issues.append(PatchPayloadValidationIssue(
            issue_id=f"ppvi_{payload.payload_id}_id_mismatch",
            category="undeclared_path",
            description=f"payload_id {payload.payload_id} != change payload_id {expected_pid}",
            payload_id=payload.payload_id,
            change_id=payload.change_id,
            resolution="regenerate",
        ))

    # 1. Validate artifact content SHA from ArtifactStore
    _validate_payload_artifact_sha(payload, artifact_store, run_id, issues)

    # 2. Validate before_sha256
    _validate_before_sha(payload, change, repository_root, issues)

    # 3. Validate target_before_sha256
    _validate_target_before_sha(payload, change, repository_root, issues)

    return issues


def _validate_payload_artifact_sha(
    payload: PatchPayload,
    artifact_store: ArtifactStore,
    run_id: str,
    issues: list[PatchPayloadValidationIssue],
) -> None:
    """Read artifact content from ArtifactStore and verify against payload_sha256."""
    try:
        actual_content = artifact_store.read_raw(run_id, payload.payload_artifact_id)
    except (FileNotFoundError, ValueError, OSError) as exc:
        issues.append(PatchPayloadValidationIssue(
            issue_id=f"ppvi_{payload.payload_id}_artifact_missing",
            category="payload_sha_mismatch",
            description=f"payload artifact not found in store: {payload.payload_artifact_id}: {exc}",
            payload_id=payload.payload_id,
            change_id=payload.change_id,
            resolution="regenerate",
        ))
        return

    actual_sha = hashlib.sha256(actual_content).hexdigest()
    if actual_sha != payload.payload_sha256:
        issues.append(PatchPayloadValidationIssue(
            issue_id=f"ppvi_{payload.payload_id}_payload_sha",
            category="payload_sha_mismatch",
            description=f"payload_sha256 mismatch for {payload.payload_artifact_id}: expected={payload.payload_sha256[:16]}, actual={actual_sha[:16]}",
            payload_id=payload.payload_id,
            change_id=payload.change_id,
            resolution="regenerate",
        ))


def _validate_before_sha(
    payload: PatchPayload,
    change: PlannedRepositoryChange,
    repository_root: Path,
    issues: list[PatchPayloadValidationIssue],
) -> None:
    """Validate before_sha256 against the source file on disk.

    For rename: source file is change.repository_path (the file being renamed).
    For modify/create: source file is payload.target_path.
    """
    source_path = repository_root / change.repository_path

    # 10. before_sha256 required for modify/rename
    if change.operation_kind in {"modify", "rename"}:
        if payload.before_sha256 is None:
            issues.append(PatchPayloadValidationIssue(
                issue_id=f"ppvi_{payload.payload_id}_before_sha_missing",
                category="before_sha_mismatch",
                description=f"before_sha256 required for {change.operation_kind} but is None",
                payload_id=payload.payload_id,
                change_id=payload.change_id,
                resolution="regenerate",
            ))
            return
        if not source_path.exists():
            issues.append(PatchPayloadValidationIssue(
                issue_id=f"ppvi_{payload.payload_id}_source_missing",
                category="before_sha_mismatch",
                description=f"source file for {change.operation_kind} does not exist: {source_path}",
                payload_id=payload.payload_id,
                change_id=payload.change_id,
                resolution="regenerate",
            ))
            return

    if payload.before_sha256 is not None:
        if source_path.exists():
            actual = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if actual != payload.before_sha256:
                issues.append(PatchPayloadValidationIssue(
                    issue_id=f"ppvi_{payload.payload_id}_before_sha",
                    category="before_sha_mismatch",
                    description=f"before_sha256 mismatch for {change.repository_path}: expected={payload.before_sha256[:16]}, actual={actual[:16]}",
                    payload_id=payload.payload_id,
                    change_id=payload.change_id,
                    resolution="regenerate",
                ))


def _validate_target_before_sha(
    payload: PatchPayload,
    change: PlannedRepositoryChange,
    repository_root: Path,
    issues: list[PatchPayloadValidationIssue],
) -> None:
    """Validate target_before_sha256 against the target file on disk.

    For rename: target is change.rename_target_path.
    For modify/create: target is payload.target_path.
    """
    target_path = repository_root / (change.rename_target_path if change.rename_target_path else payload.target_path)

    if change.target_collision_policy == "replace_existing":
        # 11. target_before_sha256 required
        if payload.target_before_sha256 is None:
            issues.append(PatchPayloadValidationIssue(
                issue_id=f"ppvi_{payload.payload_id}_target_before_missing",
                category="target_before_sha_mismatch",
                description=f"target_before_sha256 required for replace_existing but is None",
                payload_id=payload.payload_id,
                change_id=payload.change_id,
                resolution="regenerate",
            ))
            return
        if not target_path.exists():
            issues.append(PatchPayloadValidationIssue(
                issue_id=f"ppvi_{payload.payload_id}_target_missing",
                category="target_before_sha_mismatch",
                description=f"target file for replace_existing does not exist: {target_path}",
                payload_id=payload.payload_id,
                change_id=payload.change_id,
                resolution="regenerate",
            ))
            return

    if payload.target_before_sha256 is not None:
        if target_path.exists():
            actual = hashlib.sha256(target_path.read_bytes()).hexdigest()
            if actual != payload.target_before_sha256:
                issues.append(PatchPayloadValidationIssue(
                    issue_id=f"ppvi_{payload.payload_id}_target_before",
                    category="target_before_sha_mismatch",
                    description=f"target_before_sha256 mismatch for {target_path}: expected={payload.target_before_sha256[:16]}, actual={actual[:16]}",
                    payload_id=payload.payload_id,
                    change_id=payload.change_id,
                    resolution="regenerate",
                ))
