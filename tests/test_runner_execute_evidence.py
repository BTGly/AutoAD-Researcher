"""Audit: runner_execute evidence chain integration test.

Loads a completed 3.8 run from disk and verifies all evidence
chain constraints enforced by the sealed validators.
"""

import json
import hashlib
from pathlib import Path

import pytest

from autoad_researcher.schemas.execution import (
    ExecutionManifest,
    ExecutionUnitStatus,
)
from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.runner.validators import (
    derive_attempt_outcome,
    derive_execution_status,
    derive_final_status,
    derive_overall_status,
    derive_terminal_reason,
    validate_attempt_record_against_artifacts,
    validate_handoff_against_manifest,
)
from autoad_researcher.runner.models import ExperimentExecutionResult


RUN_ID = "run_l3_bottle_001"
RUNS_ROOT = Path("runs")


def _load_manifest(runs_root: Path, run_id: str) -> ExecutionManifest:
    path = runs_root / run_id / "runner_execute" / "execution_manifest.json"
    if not path.exists():
        pytest.skip(f"no execution manifest at {path}")
    with path.open() as f:
        return ExecutionManifest.model_validate(json.load(f))


def _load_execution_result(attempt_dir: Path) -> ExperimentExecutionResult:
    path = attempt_dir / "execution_result.json"
    with path.open() as f:
        return ExperimentExecutionResult.model_validate(json.load(f))


class TestRunnerExecuteEvidenceAudit:
    """Evidence chain audit for 3.8 run (handles any execution state)."""

    manifest: ExecutionManifest

    @classmethod
    def setup_class(cls):
        cls.manifest = _load_manifest(RUNS_ROOT, RUN_ID)

    # ── Overall counts ─────────────────────────────────────────────────

    def test_total_unit_count(self):
        total = len(self.manifest.unit_records)
        assert total == 3, f"expected 3 total units, got {total}"

    def test_counts_sum_to_total(self):
        total = (
            self.manifest.completed_unit_count
            + self.manifest.failed_unit_count
            + self.manifest.blocked_unit_count
        )
        assert total == len(self.manifest.unit_records), (
            f"completed({self.manifest.completed_unit_count}) + "
            f"failed({self.manifest.failed_unit_count}) + "
            f"blocked({self.manifest.blocked_unit_count}) = {total} "
            f"!= {len(self.manifest.unit_records)}"
        )

    def test_counts_match_records(self):
        completed = sum(1 for r in self.manifest.unit_records if r.final_status == ExecutionUnitStatus.COMPLETED)
        failed = sum(1 for r in self.manifest.unit_records if r.final_status == ExecutionUnitStatus.FAILED)
        blocked = sum(1 for r in self.manifest.unit_records if r.final_status == ExecutionUnitStatus.BLOCKED)
        assert self.manifest.completed_unit_count == completed
        assert self.manifest.failed_unit_count == failed
        assert self.manifest.blocked_unit_count == blocked

    def test_blocked_unit_count_is_zero(self):
        assert self.manifest.blocked_unit_count == 0

    # ── Per-unit outcome correctness ────────────────────────────────────

    def test_terminal_reasons_valid(self):
        valid_reasons = {"completed", "execution_failed", "validity_failed",
                         "insufficient_evidence", "blocked"}
        for record in self.manifest.unit_records:
            assert record.terminal_reason in valid_reasons, (
                f"{record.unit_id}: invalid terminal_reason={record.terminal_reason}"
            )

    def test_schema_consistency_per_unit(self):
        for record in self.manifest.unit_records:
            assert len(record.attempts) > 0
            for attempt in record.attempts:
                assert attempt.unit_id == record.unit_id
                assert attempt.attempt_index >= 1

    def test_identity_shas_consistent_across_attempts(self):
        for record in self.manifest.unit_records:
            shas = {a.identity.execution_unit_plan_sha256 for a in record.attempts}
            assert len(shas) == 1, (
                f"{record.unit_id}: {len(shas)} distinct unit plan SHAs"
            )

    # ── Derived status consistency ──────────────────────────────────────

    def test_derived_terminal_reason_matches(self):
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                reason = derive_terminal_reason(attempt.outcome)
                assert reason == record.terminal_reason, (
                    f"{attempt.attempt_id}: stored={record.terminal_reason}, derived={reason}"
                )

    def test_derived_final_status_matches(self):
        for record in self.manifest.unit_records:
            status = derive_final_status(record.terminal_reason)
            assert status == record.final_status, (
                f"{record.unit_id}: stored={record.final_status}, derived={status}"
            )

    def test_derived_overall_status_matches_counts(self):
        status = derive_overall_status(self.manifest)
        if self.manifest.completed_unit_count == len(self.manifest.unit_records):
            assert status == "completed", f"overall status: {status}"
        elif self.manifest.failed_unit_count > 0:
            assert status == "failed", f"overall status: {status}"
        elif self.manifest.blocked_unit_count > 0:
            assert status == "blocked", f"overall status: {status}"

    # ── Artifact SHA consistency ────────────────────────────────────────

    def test_execution_result_file_exists_and_sha_matches(self):
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                if attempt.execution_result_ref is None:
                    continue
                ref = attempt.execution_result_ref
                candidate = RUNS_ROOT / ref.locator
                if not candidate.exists():
                    candidate = RUNS_ROOT / RUN_ID / ref.locator
                if not candidate.exists():
                    continue
                actual_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
                assert ref.sha256 == actual_sha, (
                    f"exec result SHA mismatch for {ref.locator}: "
                    f"ref={ref.sha256}, file={actual_sha}"
                )

    def test_artifact_sha256_chain(self):
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                for binding in (attempt.produced_artifacts or []):
                    for b in binding.bindings:
                        artifact = b.artifact_ref
                        candidate = RUNS_ROOT / artifact.locator
                        if not candidate.exists():
                            candidate = RUNS_ROOT / RUN_ID / artifact.locator
                        if not candidate.exists():
                            continue
                        actual_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
                        assert artifact.sha256 == actual_sha, (
                            f"artifact SHA mismatch for {artifact.locator}: "
                            f"ref={artifact.sha256}, file={actual_sha}"
                        )

    def test_metrics_report_ref_sha_matches_file(self):
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                if attempt.metrics_report_ref is None:
                    continue
                ref = attempt.metrics_report_ref
                candidate = RUNS_ROOT / ref.locator
                if not candidate.exists():
                    candidate = RUNS_ROOT / RUN_ID / ref.locator
                if not candidate.exists():
                    continue
                actual_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
                assert ref.sha256 == actual_sha, (
                    f"metrics_report_ref SHA mismatch for {ref.locator}: "
                    f"ref={ref.sha256}, file={actual_sha}"
                )

    def test_validity_report_ref_sha_matches_file(self):
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                if attempt.validity_report_ref is None:
                    continue
                ref = attempt.validity_report_ref
                candidate = RUNS_ROOT / ref.locator
                if not candidate.exists():
                    candidate = RUNS_ROOT / RUN_ID / ref.locator
                if not candidate.exists():
                    continue
                actual_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
                assert ref.sha256 == actual_sha, (
                    f"validity_report_ref SHA mismatch for {ref.locator}: "
                    f"ref={ref.sha256}, file={actual_sha}"
                )

    # ── Handoff validation ──────────────────────────────────────────────

    def test_handoff_counts_match_manifest(self):
        handoff_path = RUNS_ROOT / RUN_ID / "runner_execute" / "experiment_execution_handoff.json"
        if not handoff_path.exists():
            pytest.skip("no handoff file")
        handoff_data = json.loads(handoff_path.read_text(encoding="utf-8"))
        assert len(handoff_data.get("completed_unit_ids", [])) == self.manifest.completed_unit_count
        assert len(handoff_data.get("failed_unit_ids", [])) == self.manifest.failed_unit_count

    def test_handoff_manifest_validation(self):
        try:
            validate_handoff_against_manifest(self.manifest)
        except Exception as exc:
            pytest.fail(f"handoff-manifest validation failed: {exc}")
