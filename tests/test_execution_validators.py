"""Tests for Step 3.8 execution validator functions."""

import pytest

from autoad_researcher.runner.models import ExperimentInputRefs
from autoad_researcher.runner.validators import (
    compute_identity_match,
    derive_attempt_outcome,
    derive_execution_status,
    derive_final_status,
    derive_overall_status,
    derive_terminal_reason,
    derive_workspace_execution_refs,
    validate_intake_against_patch_handoff,
    validate_resolution_presence,
)
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.execution import (
    AttemptIdentitySnapshot,
    AttemptRecord,
    AttemptOutcome,
    ExecutionUnitPlan,
    ExecutionUnitRecord,
    FailureClassification,
    PlannedArtifactBinding,
    PlannedArtifactProduction,
    ProducedArtifactRecord,
    ResolvedArtifactBinding,
    RunnerIntakeRequest,
    WorkspaceExecutionRef,
)
from autoad_researcher.schemas.patch_planning import (
    BaselineWorkspaceRef,
    PatchRunnerHandoff,
    VariantWorkspaceHandoff,
)

_SHA = "a" * 64
_HEX40 = "0123456789abcdef0123456789abcdef01234567"


def _ref(artifact_id="art"):
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type="manifest",
        locator=f"runs/run_test/{artifact_id}.json",
        sha256=_SHA,
    )


def _snapshot(unit_id="u1", attempt_number=1):
    return AttemptIdentitySnapshot(
        unit_id=unit_id,
        attempt_number=attempt_number,
        repository_fingerprint="f" * 64,
        command_sha256=_SHA,
        environment_sha256=_SHA,
        dataset_sha256=_SHA,
    )


# ── compute_identity_match ────────────────────────────────────────────


class TestComputeIdentityMatch:
    def test_match(self):
        snap = _snapshot()
        refs = ExperimentInputRefs(
            repository_fingerprint="f" * 64,
            environment_sha256=_SHA,
            dataset_manifest_sha256=_SHA,
            asset_manifest_sha256=_SHA,
            command_sha256=_SHA,
        )
        assert compute_identity_match(snap, refs) is True

    def test_command_mismatch(self):
        snap = _snapshot()
        refs = ExperimentInputRefs(
            repository_fingerprint="f" * 64,
            environment_sha256=_SHA,
            dataset_manifest_sha256=_SHA,
            asset_manifest_sha256=_SHA,
            command_sha256="b" * 64,
        )
        assert compute_identity_match(snap, refs) is False

    def test_environment_mismatch(self):
        snap = _snapshot()
        refs = ExperimentInputRefs(
            repository_fingerprint="f" * 64,
            environment_sha256="b" * 64,
            dataset_manifest_sha256=_SHA,
            asset_manifest_sha256=_SHA,
            command_sha256=_SHA,
        )
        assert compute_identity_match(snap, refs) is False

    def test_dataset_mismatch(self):
        snap = _snapshot()
        refs = ExperimentInputRefs(
            repository_fingerprint="f" * 64,
            environment_sha256=_SHA,
            dataset_manifest_sha256="b" * 64,
            asset_manifest_sha256=_SHA,
            command_sha256=_SHA,
        )
        assert compute_identity_match(snap, refs) is False


# ── derive_workspace_execution_refs ───────────────────────────────────


class TestDeriveWorkspaceExecutionRefs:
    def test_single_variant(self):
        handoff = PatchRunnerHandoff(
            run_id="run_test",
            repository_before_commit=_HEX40,
            approved_patch_plan_sha256=_SHA,
            selected_variant_ids=["v1"],
            experiment_bundle_ref="bundle_001",
            baseline_workspace_ref=BaselineWorkspaceRef(
                workspace_id="ws_base",
                repository_fingerprint="f" * 64,
                repository_commit=_HEX40,
                repository_validation_ref=_ref("val_base"),
            ),
            variant_workspaces=[
                VariantWorkspaceHandoff(
                    workspace_id="ws_v1",
                    variant_ids=["v1"],
                    repository_fingerprint="0" * 64,
                    patch_diff_sha256=_SHA,
                    local_validation_report_sha256=_SHA,
                    patch_application_manifest_ref=_ref("man_v1"),
                    post_patch_validation_report_ref=_ref("pval_v1"),
                ),
            ],
        )
        refs = derive_workspace_execution_refs(handoff)
        assert len(refs) == 2
        assert refs[0].workspace_id == "ws_base"
        assert refs[0].variant_ids == []
        assert refs[1].workspace_id == "ws_v1"
        assert refs[1].variant_ids == ["v1"]


# ── validate_intake_against_patch_handoff ─────────────────────────────


class TestValidateIntakeAgainstPatchHandoff:
    def test_passed(self):
        handoff = PatchRunnerHandoff(
            run_id="run_test",
            repository_before_commit=_HEX40,
            approved_patch_plan_sha256=_SHA,
            selected_variant_ids=["v1"],
            experiment_bundle_ref="bundle_001",
            baseline_workspace_ref=BaselineWorkspaceRef(
                workspace_id="ws_base",
                repository_fingerprint="f" * 64,
                repository_commit=_HEX40,
                repository_validation_ref=_ref("val_base"),
            ),
            variant_workspaces=[
                VariantWorkspaceHandoff(
                    workspace_id="ws_v1",
                    variant_ids=["v1"],
                    repository_fingerprint="0" * 64,
                    patch_diff_sha256=_SHA,
                    local_validation_report_sha256=_SHA,
                    patch_application_manifest_ref=_ref("man_v1"),
                    post_patch_validation_report_ref=_ref("pval_v1"),
                ),
            ],
        )
        intake = RunnerIntakeRequest(
            run_id="run_test",
            handoff_ref=_ref("handoff"),
            patch_plan_sha256=_SHA,
            workspace_execution_refs=[
                WorkspaceExecutionRef(workspace_id="ws_base", variant_ids=[]),
                WorkspaceExecutionRef(workspace_id="ws_v1", variant_ids=["v1"]),
            ],
        )
        report = validate_intake_against_patch_handoff(intake, handoff)
        assert report.overall == "passed"
        assert len(report.checks) == 3

    def test_run_id_mismatch(self):
        handoff = PatchRunnerHandoff(
            run_id="run_test",
            repository_before_commit=_HEX40,
            approved_patch_plan_sha256=_SHA,
            selected_variant_ids=["v1"],
            experiment_bundle_ref="bundle_001",
            baseline_workspace_ref=BaselineWorkspaceRef(
                workspace_id="ws_base",
                repository_fingerprint="f" * 64,
                repository_commit=_HEX40,
                repository_validation_ref=_ref("val_base"),
            ),
            variant_workspaces=[
                VariantWorkspaceHandoff(
                    workspace_id="ws_v1",
                    variant_ids=["v1"],
                    repository_fingerprint="0" * 64,
                    patch_diff_sha256=_SHA,
                    local_validation_report_sha256=_SHA,
                    patch_application_manifest_ref=_ref("man_v1"),
                    post_patch_validation_report_ref=_ref("pval_v1"),
                ),
            ],
        )
        intake = RunnerIntakeRequest(
            run_id="other_run",
            handoff_ref=_ref("handoff"),
            patch_plan_sha256=_SHA,
        )
        report = validate_intake_against_patch_handoff(intake, handoff)
        assert report.overall == "failed"


# ── derive_overall_status ─────────────────────────────────────────────


class TestDeriveOverallStatus:
    def test_all_succeeded(self):
        records = [
            ExecutionUnitRecord(
                plan=ExecutionUnitPlan(
                    unit_id="u1", workspace_id="ws_01",
                    command_plan="x", max_wall_time_seconds=3600,
                ),
                final_status="succeeded",
            ),
            ExecutionUnitRecord(
                plan=ExecutionUnitPlan(
                    unit_id="u2", workspace_id="ws_01",
                    command_plan="x", max_wall_time_seconds=3600,
                ),
                final_status="succeeded",
            ),
        ]
        assert derive_overall_status(records) == "succeeded"

    def test_mixed(self):
        records = [
            ExecutionUnitRecord(
                plan=ExecutionUnitPlan(
                    unit_id="u1", workspace_id="ws_01",
                    command_plan="x", max_wall_time_seconds=3600,
                ),
                final_status="succeeded",
            ),
            ExecutionUnitRecord(
                plan=ExecutionUnitPlan(
                    unit_id="u2", workspace_id="ws_01",
                    command_plan="x", max_wall_time_seconds=3600,
                ),
                final_status="failed",
            ),
        ]
        assert derive_overall_status(records) == "failed"

    def test_empty(self):
        assert derive_overall_status([]) == "pending"

    def test_running(self):
        records = [
            ExecutionUnitRecord(
                plan=ExecutionUnitPlan(
                    unit_id="u1", workspace_id="ws_01",
                    command_plan="x", max_wall_time_seconds=3600,
                ),
                final_status="running",
            ),
        ]
        assert derive_overall_status(records) == "running"


# ── derive_attempt_outcome ────────────────────────────────────────────


class TestDeriveAttemptOutcome:
    def test_minimal(self):
        outcome = derive_attempt_outcome(
            snapshot=_snapshot(),
            execution_result_ref=_ref("exec"),
        )
        assert outcome.identity.unit_id == "u1"
        assert outcome.execution_result_ref.artifact_id == "exec"
        assert outcome.repro_summary_refs == []

    def test_with_optional_refs(self):
        outcome = derive_attempt_outcome(
            snapshot=_snapshot(),
            execution_result_ref=_ref("exec"),
            metrics_report_ref=_ref("metrics"),
            validity_report_ref=_ref("validity"),
            repro_summary_refs=[_ref("repro")],
        )
        assert outcome.metrics_report_ref is not None
        assert outcome.validity_report_ref is not None
        assert len(outcome.repro_summary_refs) == 1


# ── derive_terminal_reason ────────────────────────────────────────────


class TestDeriveTerminalReason:
    def test_max_retries(self):
        assert derive_terminal_reason("max_retries") == "max_retries_exceeded"

    def test_wall_time(self):
        assert derive_terminal_reason("wall_time") == "total_wall_time_exceeded"

    def test_metric(self):
        assert derive_terminal_reason("metric") == "terminal_metric_failure"

    def test_environment(self):
        assert derive_terminal_reason("environment") == "terminal_environment_error"

    def test_repository(self):
        assert derive_terminal_reason("repository") == "terminal_invalid_repository"

    def test_unknown_classification_returns_none(self):
        assert derive_terminal_reason("unknown") is None


# ── derive_final_status ───────────────────────────────────────────────


class TestDeriveFinalStatus:
    def test_already_succeeded(self):
        assert derive_final_status("succeeded", None) == "succeeded"

    def test_already_failed(self):
        assert derive_final_status("failed", None) == "failed"

    def test_terminal_reason_maps_to_failed(self):
        assert derive_final_status("running", "max_retries_exceeded") == "failed"

    def test_pending_with_terminal(self):
        assert derive_final_status("pending", "terminal_metric_failure") == "failed"

    def test_none_terminal_returns_status(self):
        assert derive_final_status("pending", None) == "pending"


# ── validate_resolution_presence ──────────────────────────────────────


class TestValidateResolutionPresence:
    def test_all_resolved(self):
        planned = [
            PlannedArtifactProduction(
                unit_id="u1",
                bindings=[
                    PlannedArtifactBinding(
                        role="metrics", artifact_type="report", producing_unit_id="u1",
                    ),
                ],
            )
        ]
        produced = [
            ProducedArtifactRecord(
                unit_id="u1",
                attempt_identity=_snapshot(),
                bindings=[
                    ResolvedArtifactBinding(role="metrics", resolved_ref=_ref()),
                ],
            )
        ]
        assert validate_resolution_presence(produced, planned) is True

    def test_missing_resolution(self):
        planned = [
            PlannedArtifactProduction(
                unit_id="u1",
                bindings=[
                    PlannedArtifactBinding(
                        role="metrics", artifact_type="report", producing_unit_id="u1",
                    ),
                    PlannedArtifactBinding(
                        role="validity", artifact_type="report", producing_unit_id="u1",
                    ),
                ],
            )
        ]
        produced = [
            ProducedArtifactRecord(
                unit_id="u1",
                attempt_identity=_snapshot(),
                bindings=[
                    ResolvedArtifactBinding(role="metrics", resolved_ref=_ref()),
                ],
            )
        ]
        assert validate_resolution_presence(produced, planned) is False


# ── derive_execution_status ───────────────────────────────────────────


class TestDeriveExecutionStatus:
    def test_no_attempts(self):
        assert derive_execution_status([]) == "pending"
