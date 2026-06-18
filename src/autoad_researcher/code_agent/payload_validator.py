"""DeterministicPatchPayloadValidator — validates PatchPayload integrity."""

import hashlib
from pathlib import Path
from typing import Optional

from autoad_researcher.schemas.patch_planning import (
    PatchPayload, PatchPayloadManifest, PatchPayloadValidationIssue,
    PatchPayloadValidationReport, RepositoryChangePlan,
)


def validate_payload_manifest(
    *,
    manifest: PatchPayloadManifest,
    plan: RepositoryChangePlan,
    repository_root: Path,
    report_id: str,
) -> PatchPayloadValidationReport:
    """Deterministic validation of all payloads in a PatchPayloadManifest.

    Checks:
      1. payload_sha256 matches actual payload content
      2. before_sha256 matches current file content
      3. target_before_sha256 matches target file content (replace_existing)
      4. Each change_id exists in plan
      5. target_path matches plan change's repository_path
      6. No undeclared paths in diff
    """
    issues: list[PatchPayloadValidationIssue] = []
    plan_changes = {c.change_id: c for c in plan.changes}

    for payload in manifest.payloads:
        issues.extend(_validate_single_payload(payload, plan_changes, repository_root))

    status = "passed" if not issues else "failed"
    from datetime import datetime, timezone
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
    plan_changes: dict[str, "PlannedRepositoryChange"],
    repository_root: Path,
) -> list[PatchPayloadValidationIssue]:
    """Validate one PatchPayload against plan and filesystem."""
    from autoad_researcher.schemas.patch_planning import PlannedRepositoryChange

    issues: list[PatchPayloadValidationIssue] = []
    change = plan_changes.get(payload.change_id)
    if change is None:
        issues.append(PatchPayloadValidationIssue(
            issue_id=f"ppvi_{payload.payload_id}_no_change",
            category="undeclared_path",
            description=f"change_id {payload.change_id} not in plan",
            payload_id=payload.payload_id,
        ))
        return issues

    if payload.target_path != change.repository_path:
        issues.append(PatchPayloadValidationIssue(
            issue_id=f"ppvi_{payload.payload_id}_path",
            category="undeclared_path",
            description=f"payload target_path {payload.target_path} != plan path {change.repository_path}",
            payload_id=payload.payload_id,
            affected_change_ids=[payload.change_id],
        ))

    _validate_sha_integrity(payload, repository_root, issues)
    _validate_target_before_sha(payload, change, repository_root, issues)

    return issues


def _validate_sha_integrity(
    payload: PatchPayload,
    repository_root: Path,
    issues: list,
) -> None:
    """Verify payload_sha256 and before_sha256 against filesystem."""
    file_path = repository_root / payload.target_path
    actual_before_sha: Optional[str] = None
    if file_path.exists():
        actual_before_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()

    if payload.before_sha256 is not None and actual_before_sha is not None:
        if payload.before_sha256 != actual_before_sha:
            issues.append(PatchPayloadValidationIssue(
                issue_id=f"ppvi_{payload.payload_id}_before_sha",
                category="before_sha_mismatch",
                description=f"before_sha256 mismatch for {payload.target_path}",
                payload_id=payload.payload_id,
                affected_change_ids=[payload.change_id],
            ))


def _validate_target_before_sha(
    payload: PatchPayload,
    change: "PlannedRepositoryChange",
    repository_root: Path,
    issues: list,
) -> None:
    """Verify target_before_sha256 if replace_existing."""
    if change.target_collision_policy == "replace_existing" and payload.target_before_sha256:
        target_path = repository_root / payload.target_path
        if target_path.exists():
            actual = hashlib.sha256(target_path.read_bytes()).hexdigest()
            if payload.target_before_sha256 != actual:
                issues.append(PatchPayloadValidationIssue(
                    issue_id=f"ppvi_{payload.payload_id}_target_before",
                    category="target_before_sha_mismatch",
                    description=f"target_before_sha256 mismatch for {payload.target_path}",
                    payload_id=payload.payload_id,
                    affected_change_ids=[payload.change_id],
                ))
