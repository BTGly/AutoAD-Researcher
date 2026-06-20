"""Audit: runner_execute evidence chain integration test.

Loads a completed 3.8 run from disk and verifies all evidence
chain constraints enforced by the sealed validators.
"""

import json
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
    """Evidence chain audit for a completed 3.8 run."""

    manifest: ExecutionManifest

    @classmethod
    def setup_class(cls):
        cls.manifest = _load_manifest(RUNS_ROOT, RUN_ID)

    # ── Overall counts ─────────────────────────────────────────────────

    def test_completed_unit_count(self):
        assert self.manifest.completed_unit_count == 3, (
            f"expected 3 completed, got {self.manifest.completed_unit_count}"
        )

    def test_failed_unit_count_is_zero(self):
        assert self.manifest.failed_unit_count == 0

    def test_blocked_unit_count_is_zero(self):
        assert self.manifest.blocked_unit_count == 0

    # ── Per-unit outcome correctness ────────────────────────────────────

    def test_all_units_completed(self):
        for record in self.manifest.unit_records:
            assert record.final_status == ExecutionUnitStatus.COMPLETED, (
                f"{record.unit_id}: {record.final_status}"
            )
            assert record.terminal_reason == "completed", (
                f"{record.unit_id}: {record.terminal_reason}"
            )

    def test_all_unit_outcomes_are_valid(self):
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                assert attempt.outcome.execution_status == "succeeded", (
                    f"{attempt.attempt_id}: execution={attempt.outcome.execution_status}"
                )
                assert attempt.outcome.metrics_status == "parsed", (
                    f"{attempt.attempt_id}: metrics={attempt.outcome.metrics_status}"
                )
                assert attempt.outcome.validity_status == "valid", (
                    f"{attempt.attempt_id}: validity={attempt.outcome.validity_status}"
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
            derived = derive_final_status(record.terminal_reason)
            assert derived == record.final_status, (
                f"{record.unit_id}: stored={record.final_status}, derived={derived}"
            )

    # ── Identity SHA consistency ────────────────────────────────────────

    def test_no_placeholder_sha_in_identities(self):
        placeholder = "0" * 64
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                ident = attempt.identity
                assert ident.execution_unit_plan_sha256 != placeholder, (
                    f"{attempt.attempt_id}: execution_unit_plan_sha256 is placeholder"
                )
                assert ident.command_sha256 != placeholder, (
                    f"{attempt.attempt_id}: command_sha256 is placeholder"
                )
                assert ident.input_refs_sha256 != placeholder, (
                    f"{attempt.attempt_id}: input_refs_sha256 is placeholder"
                )

    # ── Attempt artifact reproducibility ────────────────────────────────

    def test_command_plan_sha_reproducible(self):
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                attempt_dir = (
                    RUNS_ROOT / RUN_ID / "runner_execute"
                    / "attempts" / attempt.unit_id / f"attempt_{attempt.attempt_index}"
                )
                cp_path = attempt_dir / "command_plan.json"
                assert cp_path.exists(), f"missing {cp_path}"
                stored_sha = attempt.identity.command_sha256
                # command_sha256 is the canonical SHA of the experiment's command plan
                # stored in the produced artifact bindings
                for prod in attempt.produced_artifacts:
                    for binding in prod.bindings:
                        if binding.role == "command_plan":
                            file_sha = sha256_file(cp_path)
                            assert binding.artifact_sha256 == stored_sha, (
                                f"{attempt.attempt_id}: command_plan SHA mismatch"
                            )
                            assert binding.artifact_ref.sha256 == file_sha, (
                                f"{attempt.attempt_id}: command_plan file SHA mismatch"
                            )

    def test_no_zero_artifact_shas(self):
        placeholder = "0" * 64
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                for prod in attempt.produced_artifacts:
                    for binding in prod.bindings:
                        assert binding.artifact_sha256 != placeholder, (
                            f"{attempt.attempt_id}: {binding.role} SHA is placeholder"
                        )

    # ── Manifest-level consistency ──────────────────────────────────────

    def test_handoff_validates_against_manifest(self):
        handoff_path = (
            RUNS_ROOT / RUN_ID / "runner_execute"
            / "experiment_execution_handoff.json"
        )
        assert handoff_path.exists()
        # Schema-level consistency: handoff references must match manifest
        validate_handoff_against_manifest(self.manifest)

    def test_overall_status_matches_counts(self):
        status = derive_overall_status(self.manifest)
        assert status == "completed", f"overall status: {status}"

    # ── Execution result ref consistency ────────────────────────────────

    def test_execution_result_refs_match_files(self):
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                attempt_dir = (
                    RUNS_ROOT / RUN_ID / "runner_execute"
                    / "attempts" / attempt.unit_id / f"attempt_{attempt.attempt_index}"
                )
                if attempt.execution_result_ref is None:
                    continue
                er_path = attempt_dir / "execution_result.json"
                assert er_path.exists(), f"missing {er_path}"
                file_sha = sha256_file(er_path)
                assert attempt.execution_result_ref.sha256 == file_sha, (
                    f"{attempt.attempt_id}: execution_result ref SHA mismatch"
                )

    def test_attempt_identity_shas_unique_across_units(self):
        seen: set[tuple[str, str, str]] = set()
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                ident = attempt.identity
                key = (
                    ident.execution_unit_plan_sha256,
                    ident.command_sha256,
                    ident.input_refs_sha256,
                )
                assert key not in seen, (
                    f"duplicate identity key across attempts: {key}"
                )
                seen.add(key)

    def test_execution_unit_plan_sha_unique_per_unit(self):
        shas: dict[str, str] = {}
        for record in self.manifest.unit_records:
            for attempt in record.attempts:
                sha = attempt.identity.execution_unit_plan_sha256
                if attempt.unit_id in shas:
                    assert shas[attempt.unit_id] == sha, (
                        f"{attempt.unit_id}: execution_unit_plan_sha256 differs across attempts"
                    )
                else:
                    shas[attempt.unit_id] = sha
