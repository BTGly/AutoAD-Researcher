"""Tests for Repository Intelligence R10 repair loop."""

import json
from pathlib import Path

from autoad_researcher.repository_intelligence import (
    RepairBudgetState,
    ValidationIssue,
    RepositoryValidationReport,
    repair_repository_artifacts,
)


def write_artifacts(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "repository_summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository_purpose": {
                    "claim_id": "claim_repository_purpose",
                    "status": "confirmed",
                    "confidence": "high",
                    "summary": "unsupported",
                    "evidence_ids": ["ev_missing"],
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "entrypoints.json").write_text(json.dumps({"schema_version": 1, "untouched": True}), encoding="utf-8")


def report() -> RepositoryValidationReport:
    return RepositoryValidationReport(
        schema_version=1,
        status="failed",
        checked_evidence_count=1,
        checked_artifact_count=7,
        issues=[
            ValidationIssue(
                code="CLAIM_EVIDENCE_MISSING",
                severity="error",
                location="repository_summary.json:claim_repository_purpose",
                message="missing evidence",
            )
        ],
    )


def budget(**overrides) -> RepairBudgetState:
    data = {
        "repair_tool_calls_remaining": 1,
        "repair_llm_calls_remaining": 1,
        "repairs_remaining": 1,
        "total_tool_calls_remaining": 1,
        "total_llm_calls_remaining": 1,
    }
    data.update(overrides)
    return RepairBudgetState(**data)


def test_repair_downgrades_only_affected_artifact(tmp_path: Path):
    write_artifacts(tmp_path)
    before_entrypoints = (tmp_path / "entrypoints.json").read_text(encoding="utf-8")

    result = repair_repository_artifacts(run_dir=tmp_path, validation_report=report(), budget=budget())

    payload = json.loads((tmp_path / "repository_summary.json").read_text(encoding="utf-8"))
    assert result.status == "success"
    assert result.affected_artifacts == ["repository_summary.json"]
    assert payload["repository_purpose"]["status"] == "unknown"
    assert payload["repository_purpose"]["evidence_ids"] == []
    assert (tmp_path / "entrypoints.json").read_text(encoding="utf-8") == before_entrypoints
    assert (tmp_path / "repair_attempts.jsonl").is_file()


def test_repair_budget_zero_blocks_deterministically(tmp_path: Path):
    write_artifacts(tmp_path)

    result = repair_repository_artifacts(
        run_dir=tmp_path,
        validation_report=report(),
        budget=budget(repair_llm_calls_remaining=0),
    )

    payload = json.loads((tmp_path / "repository_summary.json").read_text(encoding="utf-8"))
    assert result.status == "blocked"
    assert result.reason == "repair LLM reserve exhausted"
    assert payload["repository_purpose"]["status"] == "confirmed"


def test_non_repairable_report_is_skipped(tmp_path: Path):
    write_artifacts(tmp_path)
    validation_report = RepositoryValidationReport(
        schema_version=1,
        status="failed",
        checked_evidence_count=1,
        checked_artifact_count=7,
        issues=[
            ValidationIssue(
                code="EVIDENCE_FILE_SHA_MISMATCH",
                severity="error",
                location="ev_001",
                message="sha mismatch",
            )
        ],
    )

    result = repair_repository_artifacts(run_dir=tmp_path, validation_report=validation_report, budget=budget())

    assert result.status == "skipped"
    assert result.consumed_repair_llm_calls == 0
