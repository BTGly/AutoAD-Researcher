"""Tests for 3.8 sealed schemas — model creation, validators, constraints."""

import pytest

from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.execution import (
    AttemptIdentitySnapshot,
    AttemptOutcome,
    AttemptRecord,
    ExecutionManifest,
    ExecutionUnitPlan,
    ExecutionUnitRecord,
    ExecutionUnitResourceLedger,
    ExecutionUnitStatus,
    ExperimentExecutionHandoff,
    MatrixCoverageReport,
    PlannedArtifactBinding,
    PlannedArtifactProduction,
    ProducedArtifactRecord,
    ResolvedArtifactBinding,
    ResourceUsageReport,
    RetryDecision,
    RetryIdentity,
    WorkspaceExecutionRef,
)

_ID = "unit_01"
_ID2 = "unit_02"
_ID_WORKSPACE = "ws_01"


def _sha(seed: str = "a") -> str:
    """Return a 64-char hex string matching Sha256Pattern."""
    return (seed * 64)[:64]


def _id(name: str = "unit_01") -> str:
    """Return a valid identifier matching IdentifierPattern."""
    return name


def _ref(artifact_id="art", artifact_type="manifest") -> ArtifactReferenceV2:
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        locator=f"runs/run_test/{artifact_id}.json",
        sha256=_sha(),
    )


def _snapshot(seed: str = "a") -> AttemptIdentitySnapshot:
    return AttemptIdentitySnapshot(
        execution_unit_plan_sha256=_sha(seed),
        command_sha256=_sha(seed + "b"),
        input_refs_sha256=_sha(seed + "c"),
        workspace_repository_fingerprint="fingerprint_001",
    )


def _retry_identity(seed: str = "a") -> RetryIdentity:
    return RetryIdentity(
        execution_unit_plan_sha256=_sha(seed),
        command_sha256=_sha(seed + "b"),
        input_refs_sha256=_sha(seed + "c"),
        workspace_repository_fingerprint="fingerprint_001",
    )


def _outcome(ex_status="failed", met_status="failed", val_status="invalid") -> AttemptOutcome:
    return AttemptOutcome(
        execution_status=ex_status,
        metrics_status=met_status,
        validity_status=val_status,
    )


def _unit_plan(unit_id=_ID, workspace_id=_ID_WORKSPACE) -> ExecutionUnitPlan:
    return ExecutionUnitPlan(
        unit_id=unit_id,
        matrix_entry_id=unit_id,
        workspace_id=workspace_id,
        stage="train_and_eval",
        command_plan_sha256=_sha(),
        max_wall_time_seconds=3600,
    )


def _attempt_record(unit_id=_ID, attempt_index=1, attempt_id="att_01") -> AttemptRecord:
    snap = _snapshot()
    return AttemptRecord(
        attempt_id=attempt_id,
        attempt_index=attempt_index,
        unit_id=unit_id,
        identity=snap,
        outcome=_outcome(),
        execution_result_ref=_ref("exec"),
    )


def _unit_record(unit_id=_ID, workspace_id=_ID_WORKSPACE) -> ExecutionUnitRecord:
    plan = _unit_plan(unit_id, workspace_id)
    attempt = _attempt_record(unit_id=unit_id, attempt_index=1, attempt_id=f"{unit_id}_att1")
    return ExecutionUnitRecord(
        unit_id=unit_id,
        matrix_entry_id=unit_id,
        workspace_id=workspace_id,
        stage="train_and_eval",
        final_status=ExecutionUnitStatus.COMPLETED,
        final_attempt_id=attempt.attempt_id,
        attempts=[attempt],
        terminal_reason="completed",
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. AttemptIdentitySnapshot
# ═══════════════════════════════════════════════════════════════════════════


class TestAttemptIdentitySnapshot:
    def test_valid_construction(self):
        snap = _snapshot()
        assert len(snap.execution_unit_plan_sha256) == 64
        assert len(snap.command_sha256) == 64
        assert len(snap.input_refs_sha256) == 64
        assert snap.workspace_repository_fingerprint == "fingerprint_001"

    def test_shas_must_be_64_hex(self):
        with pytest.raises(Exception):
            AttemptIdentitySnapshot(
                execution_unit_plan_sha256="zzz" * 21 + "g",  # not hex
                command_sha256=_sha(),
                input_refs_sha256=_sha(),
                workspace_repository_fingerprint="f",
            )

    def test_short_sha_rejected(self):
        with pytest.raises(Exception):
            AttemptIdentitySnapshot(
                execution_unit_plan_sha256="abc123",
                command_sha256=_sha(),
                input_refs_sha256=_sha(),
                workspace_repository_fingerprint="f",
            )

    def test_long_sha_rejected(self):
        with pytest.raises(Exception):
            AttemptIdentitySnapshot(
                execution_unit_plan_sha256=_sha() + "00",
                command_sha256=_sha(),
                input_refs_sha256=_sha(),
                workspace_repository_fingerprint="f",
            )

    def test_fingerprint_cannot_be_empty(self):
        with pytest.raises(Exception):
            AttemptIdentitySnapshot(
                execution_unit_plan_sha256=_sha(),
                command_sha256=_sha(),
                input_refs_sha256=_sha(),
                workspace_repository_fingerprint="",
            )

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            AttemptIdentitySnapshot(
                execution_unit_plan_sha256=_sha(),
                command_sha256=_sha(),
                input_refs_sha256=_sha(),
                workspace_repository_fingerprint="f",
                extra_field="bad",
            )

    def test_uppercase_hex_rejected(self):
        with pytest.raises(Exception):
            AttemptIdentitySnapshot(
                execution_unit_plan_sha256="A" * 64,
                command_sha256="B" * 64,
                input_refs_sha256="C" * 64,
                workspace_repository_fingerprint="fp",
            )


# ═══════════════════════════════════════════════════════════════════════════
# 2. WorkspaceExecutionRef
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkspaceExecutionRefBaseline:
    def test_baseline_minimal(self):
        ref = WorkspaceExecutionRef(
            workspace_id=_ID_WORKSPACE,
            subject_type="baseline",
            repository_fingerprint="fp",
            repository_commit="abc1234",
        )
        assert ref.subject_type == "baseline"
        assert ref.variant_ids == []
        assert ref.patch_diff_sha256 is None

    def test_baseline_with_variant_ids_rejected(self):
        with pytest.raises(ValueError, match="baseline workspace must have empty variant_ids"):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="baseline",
                variant_ids=["v1"],
                repository_fingerprint="fp",
                repository_commit="abc1234",
            )

    def test_baseline_with_patch_diff_rejected(self):
        with pytest.raises(ValueError, match="baseline workspace must have patch_diff_sha256=None"):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="baseline",
                repository_fingerprint="fp",
                repository_commit="abc1234",
                patch_diff_sha256=_sha(),
            )

    def test_baseline_with_validation_report_rejected(self):
        with pytest.raises(ValueError, match="baseline workspace must have local_validation_report_sha256=None"):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="baseline",
                repository_fingerprint="fp",
                repository_commit="abc1234",
                local_validation_report_sha256=_sha(),
            )

    def test_baseline_with_patch_manifest_ref_rejected(self):
        with pytest.raises(ValueError, match="baseline workspace must have patch_application_manifest_ref=None"):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="baseline",
                repository_fingerprint="fp",
                repository_commit="abc1234",
                patch_application_manifest_ref=_ref("pm"),
            )

    def test_baseline_with_post_patch_ref_rejected(self):
        with pytest.raises(ValueError, match="baseline workspace must have post_patch_validation_report_ref=None"):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="baseline",
                repository_fingerprint="fp",
                repository_commit="abc1234",
                post_patch_validation_report_ref=_ref("pp"),
            )

    def test_baseline_extra_forbidden(self):
        with pytest.raises(Exception):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="baseline",
                repository_fingerprint="fp",
                repository_commit="abc1234",
                extra_field="bad",
            )


class TestWorkspaceExecutionRefVariant:
    def test_variant_valid(self):
        ref = WorkspaceExecutionRef(
            workspace_id=_ID_WORKSPACE,
            subject_type="variant",
            variant_ids=["v1", "v2"],
            repository_fingerprint="fp",
            repository_commit="abc1234",
            patch_diff_sha256=_sha(),
            local_validation_report_sha256=_sha(),
            patch_application_manifest_ref=_ref("pm"),
            post_patch_validation_report_ref=_ref("pp"),
        )
        assert ref.subject_type == "variant"
        assert ref.patch_diff_sha256 is not None

    def test_variant_empty_ids_rejected(self):
        with pytest.raises(ValueError, match="variant workspace must have non-empty variant_ids"):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="variant",
                variant_ids=[],
                repository_fingerprint="fp",
                repository_commit="abc1234",
                patch_diff_sha256=_sha(),
                local_validation_report_sha256=_sha(),
                patch_application_manifest_ref=_ref("pm"),
                post_patch_validation_report_ref=_ref("pp"),
            )

    def test_variant_missing_patch_diff_rejected(self):
        with pytest.raises(ValueError, match="variant workspace must have patch_diff_sha256"):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="variant",
                variant_ids=["v1"],
                repository_fingerprint="fp",
                repository_commit="abc1234",
                local_validation_report_sha256=_sha(),
                patch_application_manifest_ref=_ref("pm"),
                post_patch_validation_report_ref=_ref("pp"),
            )

    def test_variant_missing_local_validation_rejected(self):
        with pytest.raises(ValueError, match="variant workspace must have local_validation_report_sha256"):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="variant",
                variant_ids=["v1"],
                repository_fingerprint="fp",
                repository_commit="abc1234",
                patch_diff_sha256=_sha(),
                patch_application_manifest_ref=_ref("pm"),
                post_patch_validation_report_ref=_ref("pp"),
            )

    def test_variant_missing_patch_manifest_rejected(self):
        with pytest.raises(ValueError, match="variant workspace must have patch_application_manifest_ref"):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="variant",
                variant_ids=["v1"],
                repository_fingerprint="fp",
                repository_commit="abc1234",
                patch_diff_sha256=_sha(),
                local_validation_report_sha256=_sha(),
                post_patch_validation_report_ref=_ref("pp"),
            )

    def test_variant_missing_post_patch_rejected(self):
        with pytest.raises(ValueError, match="variant workspace must have post_patch_validation_report_ref"):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="variant",
                variant_ids=["v1"],
                repository_fingerprint="fp",
                repository_commit="abc1234",
                patch_diff_sha256=_sha(),
                local_validation_report_sha256=_sha(),
                patch_application_manifest_ref=_ref("pm"),
            )

    def test_variant_extra_forbidden(self):
        with pytest.raises(Exception):
            WorkspaceExecutionRef(
                workspace_id=_ID_WORKSPACE,
                subject_type="variant",
                variant_ids=["v1"],
                repository_fingerprint="fp",
                repository_commit="abc1234",
                patch_diff_sha256=_sha(),
                local_validation_report_sha256=_sha(),
                patch_application_manifest_ref=_ref("pm"),
                post_patch_validation_report_ref=_ref("pp"),
                extra_field="bad",
            )


# ═══════════════════════════════════════════════════════════════════════════
# 3. AttemptOutcome
# ═══════════════════════════════════════════════════════════════════════════


class TestAttemptOutcome:
    def test_valid(self):
        out = AttemptOutcome(
            execution_status="succeeded",
            metrics_status="passed",
            validity_status="valid",
        )
        assert out.execution_status == "succeeded"

    def test_failed_with_not_run_ok(self):
        out = AttemptOutcome(
            execution_status="failed",
            metrics_status="not_run",
            validity_status="not_run",
        )
        assert out.metrics_status == "not_run"

    def test_timeout_with_not_run_ok(self):
        out = AttemptOutcome(
            execution_status="timeout",
            metrics_status="not_run",
            validity_status="not_run",
        )
        assert out.execution_status == "timeout"

    def test_succeeded_requires_metrics_status(self):
        with pytest.raises(ValueError, match="succeeded execution requires metrics_status"):
            AttemptOutcome(
                execution_status="succeeded",
                metrics_status="not_run",
                validity_status="valid",
            )

    def test_succeeded_requires_validity_status(self):
        with pytest.raises(ValueError, match="succeeded execution requires validity_status"):
            AttemptOutcome(
                execution_status="succeeded",
                metrics_status="passed",
                validity_status="not_run",
            )

    def test_succeeded_both_not_run_rejected(self):
        with pytest.raises(ValueError):
            AttemptOutcome(
                execution_status="succeeded",
                metrics_status="not_run",
                validity_status="not_run",
            )

    def test_invalid_literal_rejected(self):
        with pytest.raises(Exception):
            AttemptOutcome(
                execution_status="unknown_status",
                metrics_status="passed",
                validity_status="valid",
            )

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            AttemptOutcome(
                execution_status="failed",
                metrics_status="not_run",
                validity_status="not_run",
                extra_field="bad",
            )


# ═══════════════════════════════════════════════════════════════════════════
# 4. ResourceUsageReport
# ═══════════════════════════════════════════════════════════════════════════


class TestResourceUsageReport:
    def test_baseline_valid(self):
        r = ResourceUsageReport(
            attempt_id=_ID,
            unit_id=_ID,
            subject_type="baseline",
            gpu_count_used=2,
            wall_time_seconds=7200,
        )
        assert r.variant_id is None
        assert r.seed is None
        assert r.actual_gpu_hours == 4.0

    def test_variant_valid(self):
        r = ResourceUsageReport(
            attempt_id=_ID,
            unit_id=_ID,
            subject_type="variant",
            variant_id="v1",
            seed=42,
            gpu_count_used=1,
            wall_time_seconds=3600,
        )
        assert r.variant_id == "v1"
        assert r.seed == 42
        assert r.actual_gpu_hours == 1.0

    def test_baseline_variant_id_not_none_rejected(self):
        with pytest.raises(ValueError, match="baseline must have variant_id=None"):
            ResourceUsageReport(
                attempt_id=_ID,
                unit_id=_ID,
                subject_type="baseline",
                variant_id="v1",
                gpu_count_used=1,
                wall_time_seconds=100,
            )

    def test_variant_missing_variant_id_rejected(self):
        with pytest.raises(ValueError, match="variant must have variant_id"):
            ResourceUsageReport(
                attempt_id=_ID,
                unit_id=_ID,
                subject_type="variant",
                gpu_count_used=1,
                wall_time_seconds=100,
            )

    def test_actual_gpu_hours_zero_gpu(self):
        r = ResourceUsageReport(
            attempt_id=_ID,
            unit_id=_ID,
            subject_type="baseline",
            gpu_count_used=0,
            wall_time_seconds=3600,
        )
        assert r.actual_gpu_hours == 0.0

    def test_actual_gpu_hours_zero_time(self):
        r = ResourceUsageReport(
            attempt_id=_ID,
            unit_id=_ID,
            subject_type="baseline",
            gpu_count_used=4,
            wall_time_seconds=0,
        )
        assert r.actual_gpu_hours == 0.0

    def test_gpu_count_negative_rejected(self):
        with pytest.raises(Exception):
            ResourceUsageReport(
                attempt_id=_ID,
                unit_id=_ID,
                subject_type="baseline",
                gpu_count_used=-1,
                wall_time_seconds=100,
            )

    def test_wall_time_negative_rejected(self):
        with pytest.raises(Exception):
            ResourceUsageReport(
                attempt_id=_ID,
                unit_id=_ID,
                subject_type="baseline",
                gpu_count_used=1,
                wall_time_seconds=-1,
            )

    def test_memory_peak_optional(self):
        r = ResourceUsageReport(
            attempt_id=_ID,
            unit_id=_ID,
            subject_type="baseline",
            gpu_count_used=1,
            wall_time_seconds=100,
        )
        assert r.memory_peak_bytes is None

    def test_memory_peak_negative_rejected(self):
        with pytest.raises(Exception):
            ResourceUsageReport(
                attempt_id=_ID,
                unit_id=_ID,
                subject_type="baseline",
                gpu_count_used=1,
                wall_time_seconds=100,
                memory_peak_bytes=-1,
            )

    def test_storage_peak_optional(self):
        r = ResourceUsageReport(
            attempt_id=_ID,
            unit_id=_ID,
            subject_type="baseline",
            gpu_count_used=1,
            wall_time_seconds=100,
        )
        assert r.storage_peak_bytes is None

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            ResourceUsageReport(
                attempt_id=_ID,
                unit_id=_ID,
                subject_type="baseline",
                gpu_count_used=1,
                wall_time_seconds=100,
                extra_field="bad",
            )


# ═══════════════════════════════════════════════════════════════════════════
# 5. AttemptRecord
# ═══════════════════════════════════════════════════════════════════════════


class TestAttemptRecord:
    def test_valid_minimal(self):
        record = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id=_ID,
            identity=_snapshot(),
            outcome=_outcome(),
        )
        assert record.attempt_id == "att_01"
        assert record.attempt_index == 1

    def test_valid_with_all_fields(self):
        record = AttemptRecord(
            attempt_id="att_01",
            attempt_index=2,
            unit_id=_ID,
            identity=_snapshot(),
            outcome=_outcome(ex_status="succeeded", met_status="passed", val_status="valid"),
            execution_result_ref=_ref("exec"),
            metrics_report_ref=_ref("metrics"),
            validity_report_ref=_ref("validity"),
            resource_usage_ref=_ref("usage"),
            resolved_bindings=[
                ResolvedArtifactBinding(
                    binding_id="b1",
                    role="metrics",
                    artifact_ref=_ref("art_metrics"),
                    artifact_sha256=_sha(),
                )
            ],
            produced_artifacts=[
                ProducedArtifactRecord(
                    unit_id=_ID,
                    attempt_id="att_01",
                    bindings=[
                        ResolvedArtifactBinding(
                            binding_id="b2",
                            role="report",
                            artifact_ref=_ref("report_art"),
                            artifact_sha256=_sha(),
                        )
                    ],
                )
            ],
        )
        assert record.metrics_report_ref is not None
        assert len(record.resolved_bindings) == 1
        assert len(record.produced_artifacts) == 1

    def test_attempt_index_must_be_ge_1(self):
        with pytest.raises(Exception):
            AttemptRecord(
                attempt_id="att_01",
                attempt_index=0,
                unit_id=_ID,
                identity=_snapshot(),
                outcome=_outcome(),
            )

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            AttemptRecord(
                attempt_id="att_01",
                attempt_index=1,
                unit_id=_ID,
                identity=_snapshot(),
                outcome=_outcome(),
                extra_field="bad",
            )


# ═══════════════════════════════════════════════════════════════════════════
# 6. RetryDecision
# ═══════════════════════════════════════════════════════════════════════════


class TestRetryDecision:
    def test_retry_same_command_valid(self):
        rd = RetryDecision(
            attempt_id="att_01",
            unit_id=_ID,
            prev_identity=_retry_identity(),
            identity_match=True,
            decision="retry_same_command",
            failure_classification="metric",
            reason="threshold not met",
        )
        assert rd.decision == "retry_same_command"

    def test_do_not_retry_valid(self):
        rd = RetryDecision(
            attempt_id="att_01",
            unit_id=_ID,
            prev_identity=_retry_identity(),
            identity_match=False,
            decision="do_not_retry",
            failure_classification="max_retries",
            reason="max retries exhausted",
        )
        assert rd.decision == "do_not_retry"

    def test_return_to_3_5_valid(self):
        rd = RetryDecision(
            attempt_id="att_01",
            unit_id=_ID,
            prev_identity=_retry_identity(),
            identity_match=False,
            decision="return_to_3_5",
            failure_classification="repository",
            reason="repo changed",
        )
        assert rd.decision == "return_to_3_5"

    def test_return_to_3_6_3_7_valid(self):
        rd = RetryDecision(
            attempt_id="att_01",
            unit_id=_ID,
            prev_identity=_retry_identity(),
            identity_match=False,
            decision="return_to_3_6_3_7",
            failure_classification="environment",
            reason="env mismatch",
        )
        assert rd.decision == "return_to_3_6_3_7"

    def test_blocked_valid(self):
        rd = RetryDecision(
            attempt_id="att_01",
            unit_id=_ID,
            prev_identity=_retry_identity(),
            identity_match=False,
            decision="blocked",
            failure_classification="wall_time",
            reason="timeout",
        )
        assert rd.decision == "blocked"

    def test_retry_requires_identity_match_true(self):
        with pytest.raises(ValueError, match="retry_same_command requires identity_match=True"):
            RetryDecision(
                attempt_id="att_01",
                unit_id=_ID,
                prev_identity=_retry_identity(),
                identity_match=False,
                decision="retry_same_command",
                failure_classification="metric",
                reason="x",
            )

    def test_non_retry_requires_identity_match_false(self):
        with pytest.raises(ValueError, match="non-retry decision requires identity_match=False"):
            RetryDecision(
                attempt_id="att_01",
                unit_id=_ID,
                prev_identity=_retry_identity(),
                identity_match=True,
                decision="do_not_retry",
                failure_classification="max_retries",
                reason="x",
            )

    def test_blocked_requires_identity_match_false(self):
        with pytest.raises(ValueError, match="non-retry decision requires identity_match=False"):
            RetryDecision(
                attempt_id="att_01",
                unit_id=_ID,
                prev_identity=_retry_identity(),
                identity_match=True,
                decision="blocked",
                failure_classification="wall_time",
                reason="x",
            )

    def test_reason_cannot_be_empty(self):
        with pytest.raises(Exception):
            RetryDecision(
                attempt_id="att_01",
                unit_id=_ID,
                prev_identity=_retry_identity(),
                identity_match=False,
                decision="do_not_retry",
                failure_classification="max_retries",
                reason="",
            )

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            RetryDecision(
                attempt_id="att_01",
                unit_id=_ID,
                prev_identity=_retry_identity(),
                identity_match=False,
                decision="do_not_retry",
                failure_classification="max_retries",
                reason="x",
                extra_field="bad",
            )


# ═══════════════════════════════════════════════════════════════════════════
# 7. ExecutionUnitRecord
# ═══════════════════════════════════════════════════════════════════════════


def _completed_unit(unit_id=_ID) -> ExecutionUnitRecord:
    return _unit_record(unit_id)


class TestExecutionUnitRecord:
    # ── valid constructions ─────────────────────────────────────────────

    def test_completed_valid(self):
        record = _completed_unit()
        assert record.final_status == ExecutionUnitStatus.COMPLETED
        assert record.terminal_reason == "completed"

    def test_blocked_upstream_valid(self):
        record = ExecutionUnitRecord(
            unit_id=_ID,
            matrix_entry_id=_ID,
            workspace_id=_ID_WORKSPACE,
            stage="train_and_eval",
            final_status=ExecutionUnitStatus.BLOCKED,
            terminal_reason="blocked_upstream_failure",
            blocking_unit_ids=[_ID2],
        )
        assert record.blocking_unit_ids == [_ID2]

    def test_preflight_failed_valid(self):
        record = ExecutionUnitRecord(
            unit_id=_ID,
            matrix_entry_id=_ID,
            workspace_id=_ID_WORKSPACE,
            stage="train_and_eval",
            final_status=ExecutionUnitStatus.BLOCKED,
            terminal_reason="preflight_failed",
            preflight_report_ref=_ref("preflight"),
        )
        assert record.preflight_report_ref is not None

    def test_intake_failed_valid(self):
        record = ExecutionUnitRecord(
            unit_id=_ID,
            matrix_entry_id=_ID,
            workspace_id=_ID_WORKSPACE,
            stage="train_and_eval",
            final_status=ExecutionUnitStatus.BLOCKED,
            terminal_reason="intake_failed",
        )
        assert record.terminal_reason == "intake_failed"

    # ── attemptful terminal_reasons ──────────────────────────────────────

    def test_attemptful_requires_attempts(self):
        with pytest.raises(ValueError, match="requires at least one attempt"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                final_attempt_id="att_01",
                attempts=[],
                terminal_reason="completed",
            )

    def test_attemptful_final_attempt_id_must_be_set(self):
        snap = _snapshot()
        attempt = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id=_ID,
            identity=snap,
            outcome=_outcome(),
            execution_result_ref=_ref("exec"),
        )
        with pytest.raises(ValueError, match="final_attempt_id must be set"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                attempts=[attempt],
                terminal_reason="completed",
            )

    def test_attemptful_final_attempt_id_must_match_last(self):
        plan_sha = _sha("00")
        snap = AttemptIdentitySnapshot(
            execution_unit_plan_sha256=plan_sha,
            command_sha256=_sha("aa"),
            input_refs_sha256=_sha("bb"),
            workspace_repository_fingerprint="fp",
        )
        a1 = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id=_ID,
            identity=snap,
            outcome=_outcome(),
        )
        a2 = AttemptRecord(
            attempt_id="att_02",
            attempt_index=2,
            unit_id=_ID,
            identity=AttemptIdentitySnapshot(
                execution_unit_plan_sha256=plan_sha,
                command_sha256=_sha("cc"),
                input_refs_sha256=_sha("dd"),
                workspace_repository_fingerprint="fp",
            ),
            outcome=_outcome(),
        )
        with pytest.raises(ValueError, match="final_attempt_id must match last attempt"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                final_attempt_id="att_01",
                attempts=[a1, a2],
                terminal_reason="completed",
            )

    def test_attemptful_blocking_unit_ids_must_be_empty(self):
        snap = _snapshot()
        attempt = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id=_ID,
            identity=snap,
            outcome=_outcome(),
        )
        with pytest.raises(ValueError, match="attemptful terminal_reason must have empty blocking_unit_ids"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                final_attempt_id="att_01",
                attempts=[attempt],
                terminal_reason="completed",
                blocking_unit_ids=[_ID2],
            )

    def test_attemptful_preflight_must_be_none(self):
        snap = _snapshot()
        attempt = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id=_ID,
            identity=snap,
            outcome=_outcome(),
        )
        with pytest.raises(ValueError, match="attemptful terminal_reason must have preflight_report_ref=None"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                final_attempt_id="att_01",
                attempts=[attempt],
                terminal_reason="completed",
                preflight_report_ref=_ref("preflight"),
            )

    # ── attempt index / identity checks ──────────────────────────────────

    def test_attempt_index_must_start_at_1(self):
        snap = _snapshot()
        attempt = AttemptRecord(
            attempt_id="att_01",
            attempt_index=2,
            unit_id=_ID,
            identity=snap,
            outcome=_outcome(),
        )
        with pytest.raises(ValueError, match="attempt_index must start at 1"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                final_attempt_id="att_01",
                attempts=[attempt],
                terminal_reason="completed",
            )

    def test_attempt_index_must_be_monotonic(self):
        plan_sha = _sha("0a")
        snap = AttemptIdentitySnapshot(
            execution_unit_plan_sha256=plan_sha,
            command_sha256=_sha("0c"),
            input_refs_sha256=_sha("e1"),
            workspace_repository_fingerprint="fp",
        )
        a1 = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id=_ID,
            identity=snap,
            outcome=_outcome(),
        )
        a2 = AttemptRecord(
            attempt_id="att_02",
            attempt_index=3,
            unit_id=_ID,
            identity=AttemptIdentitySnapshot(
                execution_unit_plan_sha256=plan_sha,
                command_sha256=_sha("0d"),
                input_refs_sha256=_sha("e2"),
                workspace_repository_fingerprint="fp",
            ),
            outcome=_outcome(),
        )
        a3 = AttemptRecord(
            attempt_id="att_03",
            attempt_index=2,
            unit_id=_ID,
            identity=AttemptIdentitySnapshot(
                execution_unit_plan_sha256=plan_sha,
                command_sha256=_sha("0e"),
                input_refs_sha256=_sha("e3"),
                workspace_repository_fingerprint="fp",
            ),
            outcome=_outcome(),
        )
        with pytest.raises(ValueError, match="attempt_index must be strictly increasing"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                final_attempt_id="att_03",
                attempts=[a1, a2, a3],
                terminal_reason="completed",
            )

    def test_duplicate_attempt_index_rejected(self):
        snap = _snapshot()
        a1 = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id=_ID,
            identity=snap,
            outcome=_outcome(),
        )
        a2 = AttemptRecord(
            attempt_id="att_02",
            attempt_index=1,
            unit_id=_ID,
            identity=_snapshot("b"),
            outcome=_outcome(),
        )
        with pytest.raises(ValueError, match="duplicate attempt_index"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                final_attempt_id="att_02",
                attempts=[a1, a2],
                terminal_reason="completed",
            )

    def test_duplicate_attempt_id_rejected(self):
        snap = _snapshot()
        a1 = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id=_ID,
            identity=snap,
            outcome=_outcome(),
        )
        a2 = AttemptRecord(
            attempt_id="att_01",
            attempt_index=2,
            unit_id=_ID,
            identity=_snapshot("b"),
            outcome=_outcome(),
        )
        with pytest.raises(ValueError, match="duplicate attempt_id"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                final_attempt_id="att_01",
                attempts=[a1, a2],
                terminal_reason="completed",
            )

    def test_attempt_unit_id_must_match(self):
        snap = _snapshot()
        attempt = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id="other_unit",
            identity=snap,
            outcome=_outcome(),
        )
        with pytest.raises(ValueError, match="attempt belongs to a different execution unit"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                final_attempt_id="att_01",
                attempts=[attempt],
                terminal_reason="completed",
            )

    def test_all_attempts_must_share_plan_sha(self):
        snap_a = _snapshot("a")
        snap_b = AttemptIdentitySnapshot(
            execution_unit_plan_sha256=_sha("de"),  # different plan sha
            command_sha256=snap_a.command_sha256,
            input_refs_sha256=snap_a.input_refs_sha256,
            workspace_repository_fingerprint=snap_a.workspace_repository_fingerprint,
        )
        a1 = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id=_ID,
            identity=snap_a,
            outcome=_outcome(),
        )
        a2 = AttemptRecord(
            attempt_id="att_02",
            attempt_index=2,
            unit_id=_ID,
            identity=snap_b,
            outcome=_outcome(),
        )
        with pytest.raises(ValueError, match="all attempts in a unit must share execution_unit_plan_sha256"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                final_attempt_id="att_02",
                attempts=[a1, a2],
                terminal_reason="completed",
            )

    # ── blocked_upstream_failure ─────────────────────────────────────────

    def test_blocked_requires_zero_attempts(self):
        with pytest.raises(ValueError, match="blocked_upstream_failure requires zero attempts"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.BLOCKED,
                final_attempt_id=None,
                attempts=[_attempt_record()],
                terminal_reason="blocked_upstream_failure",
                blocking_unit_ids=[_ID2],
            )

    def test_blocked_requires_blocking_ids(self):
        with pytest.raises(ValueError, match="blocked_upstream_failure requires blocking_unit_ids"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.BLOCKED,
                terminal_reason="blocked_upstream_failure",
                blocking_unit_ids=[],
            )

    def test_blocked_requires_blocked_status(self):
        with pytest.raises(ValueError, match="blocked_upstream_failure requires final_status=BLOCKED"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.FAILED,
                terminal_reason="blocked_upstream_failure",
                blocking_unit_ids=[_ID2],
            )

    # ── preflight_failed ─────────────────────────────────────────────────

    def test_preflight_requires_zero_attempts(self):
        with pytest.raises(ValueError, match="preflight_failed requires zero attempts"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.BLOCKED,
                attempts=[_attempt_record()],
                terminal_reason="preflight_failed",
                preflight_report_ref=_ref("preflight"),
            )

    def test_preflight_requires_preflight_report_ref(self):
        with pytest.raises(ValueError, match="preflight_failed requires preflight_report_ref"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.BLOCKED,
                terminal_reason="preflight_failed",
            )

    def test_preflight_requires_blocked_status(self):
        with pytest.raises(ValueError, match="preflight_failed requires final_status=BLOCKED"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.FAILED,
                terminal_reason="preflight_failed",
                preflight_report_ref=_ref("preflight"),
            )

    def test_preflight_blocking_ids_must_be_empty(self):
        with pytest.raises(ValueError, match="preflight_failed must have empty blocking_unit_ids"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.BLOCKED,
                terminal_reason="preflight_failed",
                preflight_report_ref=_ref("preflight"),
                blocking_unit_ids=[_ID2],
            )

    # ── intake_failed ────────────────────────────────────────────────────

    def test_intake_requires_zero_attempts(self):
        with pytest.raises(ValueError, match="intake_failed requires zero attempts"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.BLOCKED,
                attempts=[_attempt_record()],
                terminal_reason="intake_failed",
            )

    def test_intake_must_have_preflight_none(self):
        with pytest.raises(ValueError, match="intake_failed must have preflight_report_ref=None"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.BLOCKED,
                terminal_reason="intake_failed",
                preflight_report_ref=_ref("preflight"),
            )

    def test_intake_blocking_ids_must_be_empty(self):
        with pytest.raises(ValueError, match="intake_failed must have empty blocking_unit_ids"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.BLOCKED,
                terminal_reason="intake_failed",
                blocking_unit_ids=[_ID2],
            )

    def test_intake_requires_blocked_status(self):
        with pytest.raises(ValueError, match="intake_failed requires final_status=BLOCKED"):
            ExecutionUnitRecord(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                final_status=ExecutionUnitStatus.COMPLETED,
                terminal_reason="intake_failed",
            )

    # ── execution_failed with multiple attempts ──────────────────────────

    def test_execution_failed_two_attempts_valid(self):
        plan_sha = _sha("fa")
        snap = AttemptIdentitySnapshot(
            execution_unit_plan_sha256=plan_sha,
            command_sha256=_sha("fb"),
            input_refs_sha256=_sha("fc"),
            workspace_repository_fingerprint="fp",
        )
        a1 = AttemptRecord(
            attempt_id="att_01",
            attempt_index=1,
            unit_id=_ID,
            identity=snap,
            outcome=_outcome(),
        )
        a2 = AttemptRecord(
            attempt_id="att_02",
            attempt_index=2,
            unit_id=_ID,
            identity=snap,  # same identity, same plan sha — OK
            outcome=_outcome(),
        )
        record = ExecutionUnitRecord(
            unit_id=_ID,
            matrix_entry_id=_ID,
            workspace_id=_ID_WORKSPACE,
            stage="train_and_eval",
            final_status=ExecutionUnitStatus.FAILED,
            final_attempt_id="att_02",
            attempts=[a1, a2],
            terminal_reason="execution_failed",
        )
        assert record.final_status == ExecutionUnitStatus.FAILED
        assert len(record.attempts) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 8. ExecutionManifest
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionManifest:
    def test_minimal(self):
        m = ExecutionManifest(
            run_id="run_test",
            experiment_matrix_sha256=_sha(),
            protocol_fingerprint="fp",
            workspace_refs_sha256=_sha(),
            operational_guard_policy_sha256=_sha(),
            runner_intake_report_ref=_ref("intake"),
            completed_unit_count=0,
            failed_unit_count=0,
            blocked_unit_count=0,
        )
        assert m.run_id == "run_test"
        assert m.unit_records == []
        assert m.retry_decisions == []

    def test_with_unit_records(self):
        m = ExecutionManifest(
            run_id="run_test",
            experiment_matrix_sha256=_sha(),
            protocol_fingerprint="fp",
            workspace_refs_sha256=_sha(),
            operational_guard_policy_sha256=_sha(),
            runner_intake_report_ref=_ref("intake"),
            unit_records=[_completed_unit()],
            completed_unit_count=1,
            failed_unit_count=0,
            blocked_unit_count=0,
        )
        assert len(m.unit_records) == 1

    def test_counts_must_match_records(self):
        with pytest.raises(ValueError, match="completed_unit_count"):
            ExecutionManifest(
                run_id="run_test",
                experiment_matrix_sha256=_sha(),
                protocol_fingerprint="fp",
                workspace_refs_sha256=_sha(),
                operational_guard_policy_sha256=_sha(),
                runner_intake_report_ref=_ref("intake"),
                unit_records=[_completed_unit()],
                completed_unit_count=0,
                failed_unit_count=0,
                blocked_unit_count=0,
            )

    def test_failed_count_must_match(self):
        failed_record = ExecutionUnitRecord(
            unit_id=_ID,
            matrix_entry_id=_ID,
            workspace_id=_ID_WORKSPACE,
            stage="train_and_eval",
            final_status=ExecutionUnitStatus.FAILED,
            final_attempt_id="att_01",
            attempts=[_attempt_record()],
            terminal_reason="execution_failed",
        )
        with pytest.raises(ValueError, match="failed_unit_count"):
            ExecutionManifest(
                run_id="run_test",
                experiment_matrix_sha256=_sha(),
                protocol_fingerprint="fp",
                workspace_refs_sha256=_sha(),
                operational_guard_policy_sha256=_sha(),
                runner_intake_report_ref=_ref("intake"),
                unit_records=[failed_record],
                completed_unit_count=0,
                failed_unit_count=0,
                blocked_unit_count=0,
            )

    def test_blocked_count_must_match(self):
        blocked_record = ExecutionUnitRecord(
            unit_id=_ID,
            matrix_entry_id=_ID,
            workspace_id=_ID_WORKSPACE,
            stage="train_and_eval",
            final_status=ExecutionUnitStatus.BLOCKED,
            terminal_reason="intake_failed",
        )
        with pytest.raises(ValueError, match="blocked_unit_count"):
            ExecutionManifest(
                run_id="run_test",
                experiment_matrix_sha256=_sha(),
                protocol_fingerprint="fp",
                workspace_refs_sha256=_sha(),
                operational_guard_policy_sha256=_sha(),
                runner_intake_report_ref=_ref("intake"),
                unit_records=[blocked_record],
                completed_unit_count=0,
                failed_unit_count=0,
                blocked_unit_count=0,
            )

    def test_mixed_records_counts_match(self):
        m = ExecutionManifest(
            run_id="run_test",
            experiment_matrix_sha256=_sha(),
            protocol_fingerprint="fp",
            workspace_refs_sha256=_sha(),
            operational_guard_policy_sha256=_sha(),
            runner_intake_report_ref=_ref("intake"),
            unit_records=[
                _completed_unit(_ID),
                ExecutionUnitRecord(
                    unit_id=_ID2,
                    matrix_entry_id=_ID2,
                    workspace_id=_ID_WORKSPACE,
                    stage="train_and_eval",
                    final_status=ExecutionUnitStatus.FAILED,
                    final_attempt_id="att_fail",
                    attempts=[_attempt_record(unit_id=_ID2, attempt_index=1, attempt_id="att_fail")],
                    terminal_reason="execution_failed",
                ),
            ],
            completed_unit_count=1,
            failed_unit_count=1,
            blocked_unit_count=0,
        )
        assert m.completed_unit_count == 1
        assert m.failed_unit_count == 1

    def test_with_matrix_coverage(self):
        m = ExecutionManifest(
            run_id="run_test",
            experiment_matrix_sha256=_sha(),
            protocol_fingerprint="fp",
            workspace_refs_sha256=_sha(),
            operational_guard_policy_sha256=_sha(),
            runner_intake_report_ref=_ref("intake"),
            unit_records=[_completed_unit()],
            completed_unit_count=1,
            failed_unit_count=0,
            blocked_unit_count=0,
            matrix_coverage=MatrixCoverageReport(
                total_unit_count=1,
                completed_count=1,
                failed_count=0,
                blocked_count=0,
            ),
        )
        assert m.matrix_coverage is not None
        assert m.matrix_coverage.total_unit_count == 1

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            ExecutionManifest(
                run_id="run_test",
                experiment_matrix_sha256=_sha(),
                protocol_fingerprint="fp",
                workspace_refs_sha256=_sha(),
                operational_guard_policy_sha256=_sha(),
                runner_intake_report_ref=_ref("intake"),
                completed_unit_count=0,
                failed_unit_count=0,
                blocked_unit_count=0,
                extra_field="bad",
            )


# ═══════════════════════════════════════════════════════════════════════════
# 9. ExperimentExecutionHandoff
# ═══════════════════════════════════════════════════════════════════════════


class TestExperimentExecutionHandoff:
    def test_valid(self):
        handoff = ExperimentExecutionHandoff(
            run_id="run_test",
            execution_manifest_ref=_ref("manifest"),
            execution_unit_plans_sha256=_sha(),
            experiment_matrix_sha256=_sha(),
            statistical_analysis_plan_sha256=_sha(),
            protocol_fingerprint="fp",
            runner_intake_report_ref=_ref("intake"),
            resource_budget_ref=_ref("budget"),
            budget_decision_ref=_ref("budget_dec"),
            workspace_refs=[
                WorkspaceExecutionRef(
                    workspace_id=_ID_WORKSPACE,
                    subject_type="baseline",
                    repository_fingerprint="fp",
                    repository_commit="abc1234",
                )
            ],
            completed_unit_ids=[_ID],
            failed_unit_ids=[_ID2],
            blocked_unit_ids=[],
            overall_status="partially_completed",
        )
        assert handoff.overall_status == "partially_completed"
        assert handoff.next_stage == "3.9_results_analysis"

    def test_disjoint_sets_valid(self):
        handoff = ExperimentExecutionHandoff(
            run_id="run_test",
            execution_manifest_ref=_ref("manifest"),
            execution_unit_plans_sha256=_sha(),
            experiment_matrix_sha256=_sha(),
            statistical_analysis_plan_sha256=_sha(),
            protocol_fingerprint="fp",
            runner_intake_report_ref=_ref("intake"),
            resource_budget_ref=_ref("budget"),
            budget_decision_ref=_ref("budget_dec"),
            completed_unit_ids=["u1", "u2"],
            failed_unit_ids=["u3"],
            blocked_unit_ids=["u4", "u5"],
            overall_status="partially_completed",
        )
        assert len(handoff.completed_unit_ids) == 2

    def test_overlap_completed_failed_rejected(self):
        with pytest.raises(ValueError, match="unit ID sets must be disjoint"):
            ExperimentExecutionHandoff(
                run_id="run_test",
                execution_manifest_ref=_ref("manifest"),
                execution_unit_plans_sha256=_sha(),
                experiment_matrix_sha256=_sha(),
                statistical_analysis_plan_sha256=_sha(),
                protocol_fingerprint="fp",
                runner_intake_report_ref=_ref("intake"),
                resource_budget_ref=_ref("budget"),
                budget_decision_ref=_ref("budget_dec"),
                completed_unit_ids=["u1"],
                failed_unit_ids=["u1"],
                blocked_unit_ids=[],
                overall_status="failed",
            )

    def test_overlap_failed_blocked_rejected(self):
        with pytest.raises(ValueError, match="unit ID sets must be disjoint"):
            ExperimentExecutionHandoff(
                run_id="run_test",
                execution_manifest_ref=_ref("manifest"),
                execution_unit_plans_sha256=_sha(),
                experiment_matrix_sha256=_sha(),
                statistical_analysis_plan_sha256=_sha(),
                protocol_fingerprint="fp",
                runner_intake_report_ref=_ref("intake"),
                resource_budget_ref=_ref("budget"),
                budget_decision_ref=_ref("budget_dec"),
                completed_unit_ids=[],
                failed_unit_ids=["u1"],
                blocked_unit_ids=["u1"],
                overall_status="blocked",
            )

    def test_overlap_completed_blocked_rejected(self):
        with pytest.raises(ValueError, match="unit ID sets must be disjoint"):
            ExperimentExecutionHandoff(
                run_id="run_test",
                execution_manifest_ref=_ref("manifest"),
                execution_unit_plans_sha256=_sha(),
                experiment_matrix_sha256=_sha(),
                statistical_analysis_plan_sha256=_sha(),
                protocol_fingerprint="fp",
                runner_intake_report_ref=_ref("intake"),
                resource_budget_ref=_ref("budget"),
                budget_decision_ref=_ref("budget_dec"),
                completed_unit_ids=["u1"],
                failed_unit_ids=[],
                blocked_unit_ids=["u1"],
                overall_status="failed",
            )

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            ExperimentExecutionHandoff(
                run_id="run_test",
                execution_manifest_ref=_ref("manifest"),
                execution_unit_plans_sha256=_sha(),
                experiment_matrix_sha256=_sha(),
                statistical_analysis_plan_sha256=_sha(),
                protocol_fingerprint="fp",
                runner_intake_report_ref=_ref("intake"),
                resource_budget_ref=_ref("budget"),
                budget_decision_ref=_ref("budget_dec"),
                completed_unit_ids=[],
                failed_unit_ids=[],
                blocked_unit_ids=[],
                overall_status="completed",
                extra_field="bad",
            )


# ═══════════════════════════════════════════════════════════════════════════
# 10. ExecutionUnitPlan, MatrixCoverageReport, ExecutionUnitResourceLedger
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionUnitPlan:
    def test_valid_minimal(self):
        plan = ExecutionUnitPlan(
            unit_id=_ID,
            matrix_entry_id=_ID,
            workspace_id=_ID_WORKSPACE,
            stage="train_and_eval",
            command_plan_sha256=_sha(),
            max_wall_time_seconds=3600,
        )
        assert plan.max_attempts == 3
        assert plan.variant_id is None
        assert plan.seed is None

    def test_valid_with_productions(self):
        plan = ExecutionUnitPlan(
            unit_id=_ID,
            matrix_entry_id=_ID,
            workspace_id=_ID_WORKSPACE,
            stage="train_and_eval",
            command_plan_sha256=_sha(),
            max_wall_time_seconds=3600,
            planned_productions=[
                PlannedArtifactProduction(
                    unit_id=_ID,
                    bindings=[
                        PlannedArtifactBinding(
                            binding_id="b1",
                            role="metrics",
                            artifact_type="metrics_report",
                            producing_unit_id=_ID,
                        )
                    ],
                )
            ],
        )
        assert len(plan.planned_productions) == 1
        assert len(plan.planned_productions[0].bindings) == 1

    def test_max_attempts_ge_1(self):
        with pytest.raises(Exception):
            ExecutionUnitPlan(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                command_plan_sha256=_sha(),
                max_attempts=0,
                max_wall_time_seconds=3600,
            )

    def test_max_wall_time_ge_1(self):
        with pytest.raises(Exception):
            ExecutionUnitPlan(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                command_plan_sha256=_sha(),
                max_wall_time_seconds=0,
            )

    def test_command_plan_sha_must_be_hex64(self):
        with pytest.raises(Exception):
            ExecutionUnitPlan(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                command_plan_sha256="not-a-sha",
                max_wall_time_seconds=3600,
            )

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            ExecutionUnitPlan(
                unit_id=_ID,
                matrix_entry_id=_ID,
                workspace_id=_ID_WORKSPACE,
                stage="train_and_eval",
                command_plan_sha256=_sha(),
                max_wall_time_seconds=3600,
                extra_field="bad",
            )


class TestMatrixCoverageReport:
    def test_valid(self):
        report = MatrixCoverageReport(
            total_unit_count=10,
            completed_count=5,
            failed_count=3,
            blocked_count=2,
        )
        assert report.total_unit_count == 10
        assert report.completed_count == 5

    def test_counts_may_be_zero(self):
        report = MatrixCoverageReport(
            total_unit_count=0,
            completed_count=0,
            failed_count=0,
            blocked_count=0,
        )
        assert report.total_unit_count == 0

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            MatrixCoverageReport(
                total_unit_count=1,
                completed_count=0,
                failed_count=0,
                blocked_count=0,
                extra_field="bad",
            )


class TestExecutionUnitResourceLedger:
    def test_valid(self):
        ledger = ExecutionUnitResourceLedger(
            unit_id=_ID,
            resource_reports=[_ref("r1")],
            total_wall_time=3600.0,
            total_gpu_hours=2.0,
        )
        assert ledger.total_gpu_hours == 2.0
        assert len(ledger.resource_reports) == 1

    def test_total_wall_time_ge_0(self):
        with pytest.raises(Exception):
            ExecutionUnitResourceLedger(
                unit_id=_ID,
                total_wall_time=-1,
                total_gpu_hours=0,
            )

    def test_total_gpu_hours_ge_0(self):
        with pytest.raises(Exception):
            ExecutionUnitResourceLedger(
                unit_id=_ID,
                total_wall_time=0,
                total_gpu_hours=-1,
            )

    def test_empty_resource_reports_ok(self):
        ledger = ExecutionUnitResourceLedger(
            unit_id=_ID,
            total_wall_time=0,
            total_gpu_hours=0,
        )
        assert ledger.resource_reports == []

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            ExecutionUnitResourceLedger(
                unit_id=_ID,
                total_wall_time=0,
                total_gpu_hours=0,
                extra_field="bad",
            )
