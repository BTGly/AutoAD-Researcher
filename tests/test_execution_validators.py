"""Tests for Step 3.8 execution validator functions."""

import pytest

from autoad_researcher.runner.validators import (
    compute_identity_match,
    derive_attempt_outcome,
    derive_execution_status,
    derive_final_status,
    derive_overall_status,
    derive_terminal_reason,
    derive_workspace_execution_refs,
    validate_attempt_record_against_artifacts,
    validate_intake_against_patch_handoff,
    validate_resolution_presence,
)
from autoad_researcher.analysis.metrics import MetricsReport
from autoad_researcher.runner.models import ExperimentExecutionResult
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2, ResolvedArtifact
from autoad_researcher.schemas.execution import (
    AttemptIdentitySnapshot,
    AttemptOutcome,
    AttemptRecord,
    ExecutionManifest,
    ExecutionUnitRecord,
    ExecutionUnitStatus,
    ResolvedArtifactBinding,
    ResourceUsageReport,
    RunnerIntakeRequest,
    WorkspaceExecutionRef,
)
from autoad_researcher.schemas.patch_planning import (
    BaselineWorkspaceRef,
    PatchRunnerHandoff,
    VariantWorkspaceHandoff,
)
from autoad_researcher.supervisor.validity import ScientificValidityReport, ValidityCheck

_SHA = "a" * 64
_SHA2 = "b" * 64


def _ref(artifact_id="art", sha256=_SHA):
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type="report",
        locator=f"runs/run_test/{artifact_id}.json",
        sha256=sha256,
    )


def _snapshot(**kw):
    defaults = dict(
        execution_unit_plan_sha256=_SHA,
        command_sha256=_SHA,
        input_refs_sha256=_SHA,
        workspace_repository_fingerprint="f" * 64,
    )
    defaults.update(kw)
    return AttemptIdentitySnapshot(**defaults)


def _exec_result(**kw):
    defaults = dict(
        schema_version=1,
        run_id="run_test",
        attempt="attempt_001",
        command_id="cmd_001",
        command_sha256=_SHA,
        status="success",
        exit_code=0,
        timed_out=False,
        stdout_path="stdout.txt",
        stderr_path="stderr.txt",
        output_manifest_path="output_manifest.json",
        failure_code=None,
        failure_message=None,
    )
    defaults.update(kw)
    return ExperimentExecutionResult(**defaults)


def _metrics_report(**kw):
    defaults = dict(
        schema_version=1,
        metrics=[],
        required_parsed=0,
        required_total=0,
        status="passed",
        report_sha256=_SHA,
    )
    defaults.update(kw)
    return MetricsReport(**defaults)


def _validity_report(**kw):
    defaults = dict(
        schema_version=1,
        status="valid",
        checks=[
            ValidityCheck(
                check_id="execution_success",
                status="passed",
                message="execution success",
            ),
        ],
    )
    defaults.update(kw)
    return ScientificValidityReport(**defaults)


def _outcome(**kw):
    defaults = dict(
        execution_status="succeeded",
        metrics_status="parsed",
        validity_status="valid",
    )
    defaults.update(kw)
    return AttemptOutcome(**defaults)


def _resolved(payload, artifact_id="art", sha256=_SHA):
    ref = _ref(artifact_id, sha256)
    return ResolvedArtifact[type(payload)](
        ref=ref,
        verified_sha256=sha256,
        payload=payload,
    )


def _resource_report(**kw):
    defaults = dict(
        attempt_id="attempt_001",
        unit_id="u1",
        subject_type="variant",
        variant_id="v1",
        measurement_kind="measured",
        measurement_tool="nvidia-smi",
        gpu_count_used=1,
        peak_gpu_memory_mb=8000.0,
        avg_gpu_memory_mb=7000.0,
        peak_gpu_utilization_pct=95.0,
        avg_gpu_utilization_pct=85.0,
        wall_time_seconds=3600.0,
        cpu_time_seconds=1800.0,
        peak_cpu_memory_mb=4096.0,
        evidence_refs=[],
    )
    defaults.update(kw)
    return ResourceUsageReport(**defaults)


def _attempt_record(**kw):
    defaults = dict(
        attempt_id="attempt_001",
        attempt_index=1,
        unit_id="u1",
        identity=_snapshot(),
        outcome=_outcome(),
        execution_result_ref=_ref("exec"),
        metrics_report_ref=_ref("metrics"),
        validity_report_ref=_ref("validity"),
        resource_usage_ref=_ref("resource"),
        resolved_bindings=[],
        produced_artifacts=[],
    )
    defaults.update(kw)
    return AttemptRecord(**defaults)


def _completed_unit(unit_id="u1"):
    return ExecutionUnitRecord(
        unit_id=unit_id,
        matrix_entry_id="me1",
        stage="experiment",
        workspace_id="ws_01",
        final_status=ExecutionUnitStatus.COMPLETED,
        final_attempt_id="attempt_001",
        attempts=[_attempt_record(unit_id=unit_id, attempt_id="attempt_001")],
        terminal_reason="completed",
    )


def _failed_unit(unit_id="u1"):
    return ExecutionUnitRecord(
        unit_id=unit_id,
        matrix_entry_id="me1",
        stage="experiment",
        workspace_id="ws_01",
        final_status=ExecutionUnitStatus.FAILED,
        final_attempt_id="attempt_001",
        attempts=[
            _attempt_record(
                unit_id=unit_id,
                attempt_id="attempt_001",
                outcome=_outcome(
                    execution_status="failed",
                    metrics_status="not_run",
                    validity_status="not_run",
                ),
            ),
        ],
        terminal_reason="execution_failed",
    )


def _blocked_unit(unit_id="u1"):
    return ExecutionUnitRecord(
        unit_id=unit_id,
        matrix_entry_id="me1",
        stage="experiment",
        workspace_id="ws_01",
        final_status=ExecutionUnitStatus.BLOCKED,
        terminal_reason="blocked_upstream_failure",
        blocking_unit_ids=["u0"],
    )


def _manifest(**kw):
    defaults = dict(
        run_id="run_test",
        experiment_matrix_sha256=_SHA,
        protocol_fingerprint="pf" * 32,
        workspace_refs_sha256=_SHA,
        operational_guard_policy_sha256=_SHA,
        runner_intake_report_ref=_ref("intake"),
        unit_records=[],
        completed_unit_count=0,
        failed_unit_count=0,
        blocked_unit_count=0,
    )
    defaults.update(kw)
    return ExecutionManifest(**defaults)


# ── PatchRunnerHandoff helpers ───────────────────────────────────────

def _baseline_workspace_ref(workspace_id="ws_baseline"):
    return BaselineWorkspaceRef(
        workspace_id=workspace_id,
        repository_fingerprint="f" * 64,
        repository_commit="abc123",
        repository_validation_ref=_ref("repo_val", _SHA),
    )


def _variant_handoff(workspace_id="ws_v1", variant_ids=None):
    if variant_ids is None:
        variant_ids = ["v1"]
    return VariantWorkspaceHandoff(
        workspace_id=workspace_id,
        variant_ids=variant_ids,
        repository_fingerprint="f" * 64,
        patch_diff_sha256=_SHA,
        local_validation_report_sha256=_SHA,
        patch_application_manifest_ref=_ref("pam", _SHA),
        post_patch_validation_report_ref=_ref("ppv", _SHA),
    )


def _handoff(**kw):
    defaults = dict(
        schema_version=2,
        status="eligible_for_runner_intake",
        run_id="run_test",
        repository_before_commit="abc123",
        approved_patch_plan_sha256=_SHA,
        selected_variant_ids=["v1"],
        experiment_bundle_ref="exp_bundle_ref",
        baseline_workspace_ref=_baseline_workspace_ref(),
        variant_workspaces=[_variant_handoff()],
        next_stage="runner_intake",
    )
    defaults.update(kw)
    return PatchRunnerHandoff(**defaults)


def _runner_intake_request(workspace_refs=None):
    if workspace_refs is None:
        ws = WorkspaceExecutionRef(
            workspace_id="ws_baseline",
            subject_type="baseline",
            variant_ids=[],
            repository_fingerprint="f" * 64,
            repository_commit="abc123",
        )
        workspace_refs = [ws]
    return RunnerIntakeRequest(
        patch_runner_handoff_ref=_ref("handoff_ref", _SHA),
        experiment_planner_handoff_sha256=_SHA,
        experiment_matrix_sha256=_SHA,
        shared_protocol_fingerprint="pf" * 32,
        statistical_analysis_plan_sha256=_SHA,
        operational_guard_policy_sha256=_SHA,
        workspace_refs=workspace_refs,
    )


# -------------------------------------------------------------------
# 1. compute_identity_match
# -------------------------------------------------------------------


class TestComputeIdentityMatch:
    def test_all_fields_match(self):
        prev = _snapshot()
        next = _snapshot()
        assert compute_identity_match(prev, next) is True

    def test_execution_unit_plan_sha256_mismatch(self):
        prev = _snapshot()
        next = _snapshot(execution_unit_plan_sha256=_SHA2)
        assert compute_identity_match(prev, next) is False

    def test_command_sha256_mismatch(self):
        prev = _snapshot()
        next = _snapshot(command_sha256=_SHA2)
        assert compute_identity_match(prev, next) is False

    def test_input_refs_sha256_mismatch(self):
        prev = _snapshot()
        next = _snapshot(input_refs_sha256=_SHA2)
        assert compute_identity_match(prev, next) is False

    def test_workspace_repository_fingerprint_mismatch(self):
        prev = _snapshot()
        next = _snapshot(workspace_repository_fingerprint="e" * 64)
        assert compute_identity_match(prev, next) is False


# -------------------------------------------------------------------
# 2. validate_resolution_presence
# -------------------------------------------------------------------


class TestValidateResolutionPresence:
    def test_both_present_passes(self):
        validate_resolution_presence(
            _ref("x"), _resolved("payload", artifact_id="x"), "test",
        )

    def test_both_none_passes(self):
        validate_resolution_presence(None, None, "test")

    def test_ref_without_resolved_raises(self):
        with pytest.raises(ValueError, match="must appear together"):
            validate_resolution_presence(_ref(), None, "test")

    def test_resolved_without_ref_raises(self):
        with pytest.raises(ValueError, match="must appear together"):
            validate_resolution_presence(None, _resolved("x"), "test")


# -------------------------------------------------------------------
# 3. derive_execution_status
# -------------------------------------------------------------------


class TestDeriveExecutionStatus:
    def test_none_returns_not_run(self):
        assert derive_execution_status(None) == "not_run"

    def test_timed_out_returns_timeout(self):
        er = _exec_result(status="execution_failed", timed_out=True,
                         exit_code=1, output_manifest_path=None,
                         failure_code="timeout", failure_message="wall clock exceeded")
        assert derive_execution_status(er) == "timeout"

    def test_success_returns_succeeded(self):
        er = _exec_result(status="success")
        assert derive_execution_status(er) == "succeeded"

    def test_execution_failed_returns_failed(self):
        er = _exec_result(status="execution_failed", exit_code=1,
                         output_manifest_path=None,
                         failure_code="cmd", failure_message="nonzero exit")
        assert derive_execution_status(er) == "failed"

    def test_preflight_failed_returns_failed(self):
        er = _exec_result(status="preflight_failed", exit_code=1,
                         output_manifest_path=None,
                         failure_code="env", failure_message="missing dep")
        assert derive_execution_status(er) == "failed"


# -------------------------------------------------------------------
# 4. derive_attempt_outcome
# -------------------------------------------------------------------


class TestDeriveAttemptOutcome:
    def test_full_happy_path(self):
        er = _exec_result(status="success")
        mr = _metrics_report(status="passed")
        vr = _validity_report(status="valid")
        outcome = derive_attempt_outcome(er, mr, vr)
        assert outcome.execution_status == "succeeded"
        assert outcome.metrics_status == "parsed"
        assert outcome.validity_status == "valid"

    def test_metrics_passed_yields_parsed(self):
        er = _exec_result(status="success")
        mr = _metrics_report(status="passed")
        vr = _validity_report(status="valid")
        outcome = derive_attempt_outcome(er, mr, vr)
        assert outcome.metrics_status == "parsed"

    def test_metrics_failed_yields_parse_failed(self):
        er = _exec_result(status="success")
        mr = _metrics_report(status="failed")
        vr = _validity_report(status="valid")
        outcome = derive_attempt_outcome(er, mr, vr)
        assert outcome.metrics_status == "parse_failed"

    def test_none_metrics_yields_not_run(self):
        er = _exec_result(status="execution_failed", exit_code=1,
                         output_manifest_path=None,
                         failure_code="cmd", failure_message="nonzero exit")
        vr = _validity_report(status="valid")
        outcome = derive_attempt_outcome(er, None, vr)
        assert outcome.metrics_status == "not_run"
        assert outcome.execution_status == "failed"

    def test_none_validity_yields_not_run(self):
        er = _exec_result(status="execution_failed", exit_code=1,
                         output_manifest_path=None,
                         failure_code="cmd", failure_message="nonzero exit")
        mr = _metrics_report(status="passed")
        outcome = derive_attempt_outcome(er, mr, None)
        assert outcome.validity_status == "not_run"
        assert outcome.execution_status == "failed"


# -------------------------------------------------------------------
# 5. validate_attempt_record_against_artifacts
# -------------------------------------------------------------------


class TestValidateAttemptRecordAgainstArtifacts:
    def _happy_resolved(self):
        er = _exec_result(run_id="run_test", attempt="attempt_001",
                         command_sha256=_SHA)
        mr = _metrics_report()
        vr = _validity_report()
        rr = _resource_report(attempt_id="attempt_001", unit_id="u1")
        return (
            _resolved(er, "exec"),
            _resolved(mr, "metrics"),
            _resolved(vr, "validity"),
            _resolved(rr, "resource"),
        )

    def test_full_happy_path(self):
        attempt = _attempt_record()
        exec_r, metrics_r, validity_r, resource_r = self._happy_resolved()
        validate_attempt_record_against_artifacts(
            attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
        )

    def test_execution_result_ref_sha_mismatch(self):
        attempt = _attempt_record()
        exec_r, metrics_r, validity_r, resource_r = self._happy_resolved()
        attempt.execution_result_ref = _ref("exec", sha256=_SHA2)
        with pytest.raises(ValueError, match="execution_result ref.sha256 mismatch"):
            validate_attempt_record_against_artifacts(
                attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
            )

    def test_execution_result_verified_sha_mismatch(self):
        attempt = _attempt_record()
        er = _exec_result(run_id="run_test", attempt="attempt_001",
                         command_sha256=_SHA)
        mr = _metrics_report()
        vr = _validity_report()
        rr = _resource_report(attempt_id="attempt_001", unit_id="u1")
        exec_r = _resolved(er, "exec")
        exec_r.verified_sha256 = _SHA2
        metrics_r = _resolved(mr, "metrics")
        validity_r = _resolved(vr, "validity")
        resource_r = _resolved(rr, "resource")
        with pytest.raises(ValueError, match="execution_result verified SHA mismatch"):
            validate_attempt_record_against_artifacts(
                attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
            )

    def test_run_id_mismatch(self):
        attempt = _attempt_record()
        er = _exec_result(run_id="wrong_run", attempt="attempt_001",
                         command_sha256=_SHA)
        mr = _metrics_report()
        vr = _validity_report()
        rr = _resource_report(attempt_id="attempt_001", unit_id="u1")
        exec_r = _resolved(er, "exec")
        metrics_r = _resolved(mr, "metrics")
        validity_r = _resolved(vr, "validity")
        resource_r = _resolved(rr, "resource")
        with pytest.raises(ValueError, match="run_id"):
            validate_attempt_record_against_artifacts(
                attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
            )

    def test_attempt_id_mismatch(self):
        attempt = _attempt_record()
        er = _exec_result(run_id="run_test", attempt="wrong_attempt",
                         command_sha256=_SHA)
        mr = _metrics_report()
        vr = _validity_report()
        rr = _resource_report(attempt_id="attempt_001", unit_id="u1")
        exec_r = _resolved(er, "exec")
        metrics_r = _resolved(mr, "metrics")
        validity_r = _resolved(vr, "validity")
        resource_r = _resolved(rr, "resource")
        with pytest.raises(ValueError, match="attempt"):
            validate_attempt_record_against_artifacts(
                attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
            )

    def test_command_sha256_mismatch(self):
        attempt = _attempt_record()
        er = _exec_result(run_id="run_test", attempt="attempt_001",
                         command_sha256=_SHA2)
        mr = _metrics_report()
        vr = _validity_report()
        rr = _resource_report(attempt_id="attempt_001", unit_id="u1")
        exec_r = _resolved(er, "exec")
        metrics_r = _resolved(mr, "metrics")
        validity_r = _resolved(vr, "validity")
        resource_r = _resolved(rr, "resource")
        with pytest.raises(ValueError, match="command_sha256"):
            validate_attempt_record_against_artifacts(
                attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
            )

    def test_outcome_mismatch_derived_not_equal_stored(self):
        attempt = _attempt_record(
            outcome=_outcome(execution_status="failed",
                            metrics_status="not_run",
                            validity_status="not_run"),
        )
        exec_r, metrics_r, validity_r, resource_r = self._happy_resolved()
        with pytest.raises(ValueError, match="AttemptOutcome does not match"):
            validate_attempt_record_against_artifacts(
                attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
            )

    def test_resolved_binding_sha_mismatch(self):
        binding = ResolvedArtifactBinding(
            binding_id="b1",
            role="metrics",
            artifact_ref=_ref("metrics"),
            artifact_sha256=_SHA2,
        )
        attempt = _attempt_record(resolved_bindings=[binding])
        exec_r, metrics_r, validity_r, resource_r = self._happy_resolved()
        with pytest.raises(ValueError, match="resolved_binding SHA mismatch"):
            validate_attempt_record_against_artifacts(
                attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
            )

    def test_resource_report_attempt_id_mismatch(self):
        attempt = _attempt_record()
        er = _exec_result(run_id="run_test", attempt="attempt_001",
                         command_sha256=_SHA)
        mr = _metrics_report()
        vr = _validity_report()
        rr = _resource_report(attempt_id="wrong_attempt", unit_id="u1")
        exec_r = _resolved(er, "exec")
        metrics_r = _resolved(mr, "metrics")
        validity_r = _resolved(vr, "validity")
        resource_r = _resolved(rr, "resource")
        with pytest.raises(ValueError, match="resource_report.attempt_id"):
            validate_attempt_record_against_artifacts(
                attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
            )

    def test_resource_report_unit_id_mismatch(self):
        attempt = _attempt_record()
        er = _exec_result(run_id="run_test", attempt="attempt_001",
                         command_sha256=_SHA)
        mr = _metrics_report()
        vr = _validity_report()
        rr = _resource_report(attempt_id="attempt_001", unit_id="wrong_unit")
        exec_r = _resolved(er, "exec")
        metrics_r = _resolved(mr, "metrics")
        validity_r = _resolved(vr, "validity")
        resource_r = _resolved(rr, "resource")
        with pytest.raises(ValueError, match="resource_report.unit_id"):
            validate_attempt_record_against_artifacts(
                attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
            )

    def test_resource_report_ref_sha_mismatch(self):
        attempt = _attempt_record()
        attempt.resource_usage_ref = _ref("resource", sha256=_SHA2)
        exec_r, metrics_r, validity_r, resource_r = self._happy_resolved()
        with pytest.raises(ValueError, match="resource_report ref.sha256 mismatch"):
            validate_attempt_record_against_artifacts(
                attempt, "run_test", exec_r, metrics_r, validity_r, resource_r,
            )


# -------------------------------------------------------------------
# 6. derive_terminal_reason
# -------------------------------------------------------------------


class TestDeriveTerminalReason:
    def test_all_three_fields_completed(self):
        o = _outcome(execution_status="succeeded",
                    metrics_status="parsed",
                    validity_status="valid")
        assert derive_terminal_reason(o) == "completed"

    def test_metrics_parse_failed_not_completed(self):
        o = _outcome(execution_status="succeeded",
                    metrics_status="parse_failed",
                    validity_status="valid")
        assert derive_terminal_reason(o) == "execution_failed"

    def test_insufficient_evidence(self):
        o = _outcome(execution_status="succeeded",
                    metrics_status="parsed",
                    validity_status="insufficient_evidence")
        assert derive_terminal_reason(o) == "insufficient_evidence"

    def test_invalid_yields_validity_failed(self):
        o = _outcome(execution_status="succeeded",
                    metrics_status="parsed",
                    validity_status="invalid")
        assert derive_terminal_reason(o) == "validity_failed"

    def test_timeout_yields_execution_failed(self):
        o = _outcome(execution_status="timeout",
                    metrics_status="not_run",
                    validity_status="not_run")
        assert derive_terminal_reason(o) == "execution_failed"

    def test_failed_yields_execution_failed(self):
        o = _outcome(execution_status="failed",
                    metrics_status="not_run",
                    validity_status="not_run")
        assert derive_terminal_reason(o) == "execution_failed"

    def test_not_run_yields_execution_failed(self):
        o = _outcome(execution_status="not_run",
                    metrics_status="not_run",
                    validity_status="not_run")
        assert derive_terminal_reason(o) == "execution_failed"


# -------------------------------------------------------------------
# 7. derive_final_status
# -------------------------------------------------------------------


class TestDeriveFinalStatus:
    def test_completed_yields_completed_enum(self):
        assert derive_final_status("completed") == ExecutionUnitStatus.COMPLETED

    def test_execution_failed_yields_failed_enum(self):
        assert derive_final_status("execution_failed") == ExecutionUnitStatus.FAILED

    def test_validity_failed_yields_failed_enum(self):
        assert derive_final_status("validity_failed") == ExecutionUnitStatus.FAILED

    def test_insufficient_evidence_yields_blocked_enum(self):
        assert derive_final_status("insufficient_evidence") == ExecutionUnitStatus.BLOCKED

    def test_blocked_upstream_failure_yields_blocked_enum(self):
        assert derive_final_status("blocked_upstream_failure") == ExecutionUnitStatus.BLOCKED

    def test_intake_failed_yields_blocked_enum(self):
        assert derive_final_status("intake_failed") == ExecutionUnitStatus.BLOCKED

    def test_preflight_failed_yields_blocked_enum(self):
        assert derive_final_status("preflight_failed") == ExecutionUnitStatus.BLOCKED


# -------------------------------------------------------------------
# 8. derive_workspace_execution_refs
# -------------------------------------------------------------------


class TestDeriveWorkspaceExecutionRefs:
    def test_baseline_only(self):
        handoff = _handoff(
            selected_variant_ids=[],
            variant_workspaces=[],
        )
        refs = derive_workspace_execution_refs(handoff)
        assert len(refs) == 1
        assert refs[0].workspace_id == "ws_baseline"
        assert refs[0].subject_type == "baseline"
        assert refs[0].variant_ids == []
        assert refs[0].repository_fingerprint == "f" * 64
        assert refs[0].repository_commit == "abc123"
        assert refs[0].patch_diff_sha256 is None
        assert refs[0].local_validation_report_sha256 is None
        assert refs[0].patch_application_manifest_ref is None
        assert refs[0].post_patch_validation_report_ref is None

    def test_baseline_plus_one_variant(self):
        handoff = _handoff(
            selected_variant_ids=["v1"],
            variant_workspaces=[_variant_handoff("ws_v1", ["v1"])],
        )
        refs = derive_workspace_execution_refs(handoff)
        assert len(refs) == 2
        assert refs[0].subject_type == "baseline"
        assert refs[1].subject_type == "variant"
        assert refs[1].workspace_id == "ws_v1"
        assert refs[1].variant_ids == ["v1"]
        assert refs[1].repository_fingerprint == "f" * 64
        assert refs[1].repository_commit == "abc123"
        assert refs[1].patch_diff_sha256 == _SHA
        assert refs[1].local_validation_report_sha256 == _SHA
        assert refs[1].patch_application_manifest_ref == _ref("pam", _SHA)
        assert refs[1].post_patch_validation_report_ref == _ref("ppv", _SHA)

    def test_baseline_plus_multiple_variants(self):
        handoff = _handoff(
            selected_variant_ids=["v1", "v2"],
            variant_workspaces=[
                _variant_handoff("ws_v1", ["v1"]),
                _variant_handoff("ws_v2", ["v2"]),
            ],
        )
        refs = derive_workspace_execution_refs(handoff)
        assert len(refs) == 3
        assert refs[0].subject_type == "baseline"
        assert refs[1].subject_type == "variant"
        assert refs[1].workspace_id == "ws_v1"
        assert refs[2].subject_type == "variant"
        assert refs[2].workspace_id == "ws_v2"


# -------------------------------------------------------------------
# 9. validate_intake_against_patch_handoff
# -------------------------------------------------------------------


class TestValidateIntakeAgainstPatchHandoff:
    def test_matching_workspace_refs_passes(self):
        handoff = _handoff(
            selected_variant_ids=["v1"],
            variant_workspaces=[_variant_handoff("ws_v1", ["v1"])],
        )
        expected_refs = derive_workspace_execution_refs(handoff)
        request = _runner_intake_request(workspace_refs=expected_refs)
        validate_intake_against_patch_handoff(request, handoff)

    def test_mismatched_workspace_refs_raises(self):
        handoff = _handoff(
            selected_variant_ids=["v1"],
            variant_workspaces=[_variant_handoff("ws_v1", ["v1"])],
        )
        ws = WorkspaceExecutionRef(
            workspace_id="ws_baseline",
            subject_type="baseline",
            variant_ids=[],
            repository_fingerprint="f" * 64,
            repository_commit="abc123",
        )
        request = _runner_intake_request(workspace_refs=[ws])
        with pytest.raises(ValueError, match="workspace_refs do not match PatchRunnerHandoff"):
            validate_intake_against_patch_handoff(request, handoff)


# -------------------------------------------------------------------
# 10. derive_overall_status
# -------------------------------------------------------------------


class TestDeriveOverallStatus:
    def test_all_completed(self):
        m = _manifest(
            unit_records=[_completed_unit("u1"), _completed_unit("u2")],
            completed_unit_count=2,
        )
        assert derive_overall_status(m) == "completed"

    def test_all_failed(self):
        m = _manifest(
            unit_records=[_failed_unit("u1"), _failed_unit("u2")],
            failed_unit_count=2,
        )
        assert derive_overall_status(m) == "failed"

    def test_all_blocked(self):
        m = _manifest(
            unit_records=[_blocked_unit("u1"), _blocked_unit("u2")],
            blocked_unit_count=2,
        )
        assert derive_overall_status(m) == "blocked"

    def test_mixed_returns_partially_completed(self):
        m = _manifest(
            unit_records=[_completed_unit("u1"), _failed_unit("u2")],
            completed_unit_count=1,
            failed_unit_count=1,
        )
        assert derive_overall_status(m) == "partially_completed"

