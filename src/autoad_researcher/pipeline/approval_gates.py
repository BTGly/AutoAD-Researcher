"""Approval gate enforcement for Phase 2C HITL checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.schemas.approvals import Stage3Approval
from autoad_researcher.schemas.stage3_acceptance import Stage3AcceptanceStageRecord

GateStage = Literal["patch_applicator", "runner_execute"]
GateName = Literal["patch_approval", "run_approval"]
GateStatus = Literal["passed", "blocked"]

_SECRET_LIKE_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


class ApprovalGateReport(BaseModel):
    """Audit report for a single approval gate check."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    stage: GateStage
    gate_name: GateName
    status: GateStatus
    required_artifact: str
    observed_artifact_sha256: str | None = None
    decision: str | None = None
    blocked_reason: str | None = None
    checked_at: str


class ApprovalGateResult(BaseModel):
    """Return value for approval gate helpers."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    report: ApprovalGateReport
    blocked_record: Stage3AcceptanceStageRecord | None = None


def require_patch_approval(run_id: str, run_dir: Path, stage_dir: Path) -> ApprovalGateResult:
    """Require explicit patch approval before applying a patch."""
    approval_rel = "approvals/patch_approval.json"
    request_rel = "patch_planner/patch_planner_approval_request.json"
    approval_path = run_dir / approval_rel
    request_path = run_dir / request_rel

    if not approval_path.is_file():
        return _blocked(run_id, stage_dir, "patch_applicator", "patch_approval", approval_rel,
                        "blocked_missing_approval:patch_approval")
    if _contains_secret_like(approval_path):
        return _blocked(run_id, stage_dir, "patch_applicator", "patch_approval", approval_rel,
                        "blocked_invalid_approval:patch_approval")
    if not request_path.is_file():
        return _blocked(run_id, stage_dir, "patch_applicator", "patch_approval", request_rel,
                        "blocked_missing_artifact:patch_planner_approval_request.json")

    try:
        approval = Stage3Approval.model_validate_json(approval_path.read_text(encoding="utf-8"))
    except Exception:
        return _blocked(run_id, stage_dir, "patch_applicator", "patch_approval", approval_rel,
                        "blocked_invalid_approval:patch_approval")

    if approval.run_id != run_id or approval.decision_type != "patch_approval":
        return _blocked(run_id, stage_dir, "patch_applicator", "patch_approval", approval_rel,
                        "blocked_invalid_approval:patch_approval", decision=approval.decision_type)
    if not approval.confirmed_by_user:
        return _blocked(run_id, stage_dir, "patch_applicator", "patch_approval", approval_rel,
                        "blocked_rejected_approval:patch_approval", decision=approval.decision_type)

    return _passed(run_id, stage_dir, "patch_applicator", "patch_approval", approval_rel,
                   observed_path=approval_path, decision=approval.decision_type)


def require_run_approval(run_id: str, run_dir: Path, stage_dir: Path) -> ApprovalGateResult:
    """Require explicit real execution approval before runner execution."""
    approval_rel = "approvals/run_approval.json"
    handoff_rel = "patch_applicator/patch_runner_handoff.json"
    approval_path = run_dir / approval_rel
    handoff_path = run_dir / handoff_rel

    if not approval_path.is_file():
        return _blocked(run_id, stage_dir, "runner_execute", "run_approval", approval_rel,
                        "blocked_missing_approval:run_approval")
    if _contains_secret_like(approval_path):
        return _blocked(run_id, stage_dir, "runner_execute", "run_approval", approval_rel,
                        "blocked_invalid_approval:run_approval")
    if not handoff_path.is_file():
        return _blocked(run_id, stage_dir, "runner_execute", "run_approval", handoff_rel,
                        "blocked_missing_artifact:patch_runner_handoff.json")

    try:
        approval = Stage3Approval.model_validate_json(approval_path.read_text(encoding="utf-8"))
    except Exception:
        return _blocked(run_id, stage_dir, "runner_execute", "run_approval", approval_rel,
                        "blocked_invalid_approval:run_approval")

    if approval.run_id != run_id or approval.decision_type != "run_approval":
        return _blocked(run_id, stage_dir, "runner_execute", "run_approval", approval_rel,
                        "blocked_invalid_approval:run_approval", decision=approval.decision_type)
    if not approval.confirmed_by_user:
        return _blocked(run_id, stage_dir, "runner_execute", "run_approval", approval_rel,
                        "blocked_rejected_approval:run_approval", decision=approval.decision_type)
    if not os.environ.get("AUTOAD_L3_REAL_EXECUTION_ALLOWED"):
        return _blocked(run_id, stage_dir, "runner_execute", "run_approval", approval_rel,
                        "blocked_real_execution_not_allowed:run_approval", decision=approval.decision_type)

    return _passed(run_id, stage_dir, "runner_execute", "run_approval", approval_rel,
                   observed_path=approval_path, decision=approval.decision_type)


def _passed(
    run_id: str,
    stage_dir: Path,
    stage: GateStage,
    gate_name: GateName,
    required_artifact: str,
    *,
    observed_path: Path,
    decision: str,
) -> ApprovalGateResult:
    report = ApprovalGateReport(
        run_id=run_id,
        stage=stage,
        gate_name=gate_name,
        status="passed",
        required_artifact=required_artifact,
        observed_artifact_sha256=_sha256_file(observed_path),
        decision=decision,
        checked_at=_now(),
    )
    _write_report(stage_dir, report)
    return ApprovalGateResult(passed=True, report=report)


def _blocked(
    run_id: str,
    stage_dir: Path,
    stage: GateStage,
    gate_name: GateName,
    required_artifact: str,
    blocked_reason: str,
    *,
    decision: str | None = None,
) -> ApprovalGateResult:
    report = ApprovalGateReport(
        run_id=run_id,
        stage=stage,
        gate_name=gate_name,
        status="blocked",
        required_artifact=required_artifact,
        decision=decision,
        blocked_reason=blocked_reason,
        checked_at=_now(),
    )
    _write_report(stage_dir, report)
    record = Stage3AcceptanceStageRecord(
        stage=stage,
        status="blocked",
        blocked_reason=blocked_reason,
    )
    return ApprovalGateResult(passed=False, report=report, blocked_record=record)


def _write_report(stage_dir: Path, report: ApprovalGateReport) -> None:
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "approval_gate_report.json").write_text(
        json.dumps(report.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_secret_like(path: Path) -> bool:
    try:
        return _SECRET_LIKE_RE.search(path.read_text(encoding="utf-8")) is not None
    except UnicodeDecodeError:
        return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
