"""Bounded repository artifact repair loop for Step 3.1 R10."""

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.repository_intelligence.validate import RepositoryValidationReport

REPAIRABLE_CODES = {
    "CLAIM_CONFIRMED_WITHOUT_EVIDENCE",
    "CLAIM_EVIDENCE_MISSING",
    "CLAIM_INFERRED_WITHOUT_RATIONALE",
}


class RepairBudgetState(BaseModel):
    """Remaining repair and global budget."""

    model_config = ConfigDict(extra="forbid")

    repair_tool_calls_remaining: int = Field(ge=0)
    repair_llm_calls_remaining: int = Field(ge=0)
    repairs_remaining: int = Field(ge=0)
    total_tool_calls_remaining: int = Field(ge=0)
    total_llm_calls_remaining: int = Field(ge=0)


class RepairAttemptRecord(BaseModel):
    """One repair attempt audit record."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    status: Literal["success", "blocked", "skipped"]
    reason: str
    affected_artifacts: list[str] = Field(default_factory=list)
    consumed_repair_tool_calls: int = Field(ge=0)
    consumed_repair_llm_calls: int = Field(ge=0)
    consumed_total_tool_calls: int = Field(ge=0)
    consumed_total_llm_calls: int = Field(ge=0)


def repair_repository_artifacts(
    *,
    run_dir: Path,
    validation_report: RepositoryValidationReport,
    budget: RepairBudgetState,
) -> RepairAttemptRecord:
    """Repair artifact claim evidence issues within explicit repair budget."""
    repairable = [issue for issue in validation_report.issues if issue.code in REPAIRABLE_CODES]
    if not repairable:
        record = RepairAttemptRecord(
            schema_version=1,
            status="skipped",
            reason="validation report contains no repairable artifact claim issues",
            consumed_repair_tool_calls=0,
            consumed_repair_llm_calls=0,
            consumed_total_tool_calls=0,
            consumed_total_llm_calls=0,
        )
        _append_jsonl(run_dir / "repair_attempts.jsonl", record)
        return record

    if budget.repairs_remaining < 1:
        return _blocked(run_dir, "repair attempt budget exhausted")
    if budget.repair_llm_calls_remaining < 1:
        return _blocked(run_dir, "repair LLM reserve exhausted")
    if budget.total_llm_calls_remaining < 1:
        return _blocked(run_dir, "global LLM call budget exhausted")

    artifact_to_claims: dict[str, set[str]] = {}
    for issue in repairable:
        artifact, claim_id = _parse_issue_location(issue.location)
        if artifact is None or claim_id is None:
            continue
        artifact_to_claims.setdefault(artifact, set()).add(claim_id)

    affected: list[str] = []
    for artifact, claim_ids in artifact_to_claims.items():
        path = run_dir / artifact
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        repaired = _downgrade_claims(payload, claim_ids)
        if repaired:
            _write_json_replace(path, payload)
            affected.append(artifact)

    record = RepairAttemptRecord(
        schema_version=1,
        status="success" if affected else "skipped",
        reason="downgraded unsupported artifact claims to unknown" if affected else "no matching artifact claims found",
        affected_artifacts=sorted(affected),
        consumed_repair_tool_calls=0,
        consumed_repair_llm_calls=1 if affected else 0,
        consumed_total_tool_calls=0,
        consumed_total_llm_calls=1 if affected else 0,
    )
    _append_jsonl(run_dir / "repair_attempts.jsonl", record)
    return record


def _blocked(run_dir: Path, reason: str) -> RepairAttemptRecord:
    record = RepairAttemptRecord(
        schema_version=1,
        status="blocked",
        reason=reason,
        consumed_repair_tool_calls=0,
        consumed_repair_llm_calls=0,
        consumed_total_tool_calls=0,
        consumed_total_llm_calls=0,
    )
    _append_jsonl(run_dir / "repair_attempts.jsonl", record)
    return record


def _parse_issue_location(location: str) -> tuple[str | None, str | None]:
    if ":" not in location:
        return None, None
    artifact, claim_id = location.split(":", 1)
    return artifact, claim_id


def _downgrade_claims(payload: Any, claim_ids: set[str]) -> bool:
    repaired = False
    if isinstance(payload, dict):
        if payload.get("claim_id") in claim_ids:
            payload["status"] = "unknown"
            payload["confidence"] = "low"
            payload["evidence_ids"] = []
            payload.pop("rationale_summary", None)
            payload["summary"] = f"Unsupported claim downgraded during repair: {payload.get('summary', '')}"
            repaired = True
        for value in payload.values():
            repaired = _downgrade_claims(value, claim_ids) or repaired
    elif isinstance(payload, list):
        for value in payload:
            repaired = _downgrade_claims(value, claim_ids) or repaired
    return repaired


def _write_json_replace(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        with tmp.open("wb") as f:
            f.write(data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _append_jsonl(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    with path.open("ab") as f:
        f.write(data.encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())
