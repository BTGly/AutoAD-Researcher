"""Tests for 3.8 execution schemas — model creation, validators, constraints."""

import pytest

from autoad_researcher.schemas.artifacts import ArtifactReferenceV2, ResolvedArtifact
from autoad_researcher.schemas.execution import (
    AttemptIdentitySnapshot,
    AttemptOutcome,
    AttemptRecord,
    ExecutionManifest,
    ExecutionUnitPlan,
    ExecutionUnitRecord,
    ExecutionUnitResourceLedger,
    ExperimentExecutionHandoff,
    IntakeCheck,
    PlannedArtifactBinding,
    PlannedArtifactProduction,
    ProducedArtifactRecord,
    ResolvedArtifactBinding,
    ResourceUsageReport,
    RetryDecision,
    RetryIdentity,
    RunnerIntakeReport,
    RunnerIntakeRequest,
    WorkspaceExecutionRef,
)

_SHA = "a" * 64
_HEX40 = "0123456789abcdef0123456789abcdef01234567"


def _ref(artifact_id="art", artifact_type="manifest"):
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        locator=f"runs/run_test/{artifact_id}.json",
        sha256=_SHA,
    )


def _snapshot(unit_id="unit_01", attempt_number=1):
    return AttemptIdentitySnapshot(
        unit_id=unit_id,
        attempt_number=attempt_number,
        repository_fingerprint="f" * 64,
        command_sha256=_SHA,
        environment_sha256=_SHA,
        dataset_sha256=_SHA,
    )


# ── Intake layer ──────────────────────────────────────────────────────


class TestWorkspaceExecutionRef:
    def test_minimal(self):
        ref = WorkspaceExecutionRef(workspace_id="ws_01")
        assert ref.workspace_id == "ws_01"
        assert ref.variant_ids == []

    def test_with_variants(self):
        ref = WorkspaceExecutionRef(workspace_id="ws_01", variant_ids=["v1", "v2"])
        assert ref.variant_ids == ["v1", "v2"]

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            WorkspaceExecutionRef(workspace_id="ws_01", extra="bad")


class TestRunnerIntakeRequest:
    def test_minimal(self):
        req = RunnerIntakeRequest(
            run_id="run_test",
            handoff_ref=_ref("handoff"),
            patch_plan_sha256=_SHA,
        )
        assert req.run_id == "run_test"

    def test_with_workspace_refs(self):
        refs = [WorkspaceExecutionRef(workspace_id="ws_01")]
        req = RunnerIntakeRequest(
            run_id="run_test",
            handoff_ref=_ref("handoff"),
            patch_plan_sha256=_SHA,
            workspace_execution_refs=refs,
        )
        assert len(req.workspace_execution_refs) == 1


class TestIntakeCheck:
    def test_passed(self):
        c = IntakeCheck(name="check_1", status="passed")
        assert c.status == "passed"

    def test_failed_with_details(self):
        c = IntakeCheck(name="check_1", status="failed", details="mismatch")
        assert c.details == "mismatch"


class TestRunnerIntakeReport:
    def test_passed_overall(self):
        report = RunnerIntakeReport(
            overall="passed",
            checks=[IntakeCheck(name="c1", status="passed")],
        )
        assert report.overall == "passed"

    def test_failed_overall(self):
        report = RunnerIntakeReport(overall="failed")
        assert report.overall == "failed"


# ── Planned state ─────────────────────────────────────────────────────


class TestPlannedArtifactBinding:
    def test_valid(self):
        b = PlannedArtifactBinding(
            role="metrics", artifact_type="metrics_report", producing_unit_id="u1"
        )
        assert b.role == "metrics"


class TestPlannedArtifactProduction:
    def test_valid(self):
        p = PlannedArtifactProduction(
            unit_id="u1",
            bindings=[
                PlannedArtifactBinding(
                    role="metrics", artifact_type="metrics_report", producing_unit_id="u1"
                )
            ],
        )
        assert len(p.bindings) == 1


class TestExecutionUnitPlan:
    def test_valid(self):
        plan = ExecutionUnitPlan(
            unit_id="u1",
            workspace_id="ws_01",
            command_plan="train_and_eval",
            max_attempts=3,
            max_wall_time_seconds=3600,
        )
        assert plan.max_attempts == 3

    def test_valid_with_productions(self):
        plan = ExecutionUnitPlan(
            unit_id="u1",
            workspace_id="ws_01",
            command_plan="train_and_eval",
            max_wall_time_seconds=3600,
            planned_productions=[
                PlannedArtifactProduction(
                    unit_id="u1",
                    bindings=[
                        PlannedArtifactBinding(
                            role="metrics", artifact_type="metrics_report",
                            producing_unit_id="u1",
                        )
                    ],
                )
            ],
        )
        assert len(plan.planned_productions) == 1

    def test_max_attempts_ge_1(self):
        with pytest.raises(Exception):
            ExecutionUnitPlan(
                unit_id="u1", workspace_id="ws_01", command_plan="x",
                max_attempts=0, max_wall_time_seconds=3600,
            )

    def test_max_wall_time_ge_1(self):
        with pytest.raises(Exception):
            ExecutionUnitPlan(
                unit_id="u1", workspace_id="ws_01", command_plan="x",
                max_wall_time_seconds=0,
            )


# ── Runtime state ─────────────────────────────────────────────────────


class TestAttemptIdentitySnapshot:
    def test_valid(self):
        s = _snapshot()
        assert s.unit_id == "unit_01"
        assert s.attempt_number == 1

    def test_attempt_number_ge_1(self):
        with pytest.raises(Exception):
            _snapshot(attempt_number=0)


class TestAttemptOutcome:
    def test_valid(self):
        outcome = AttemptOutcome(
            identity=_snapshot(),
            execution_result_ref=_ref("exec"),
        )
        assert outcome.execution_result_ref.artifact_id == "exec"

    def test_with_all_refs(self):
        outcome = AttemptOutcome(
            identity=_snapshot(),
            execution_result_ref=_ref("exec"),
            metrics_report_ref=_ref("metrics"),
            validity_report_ref=_ref("validity"),
            repro_summary_refs=[_ref("repro")],
        )
        assert len(outcome.repro_summary_refs) == 1

    def test_identity_consistency_validator_passes(self):
        outcome = AttemptOutcome(
            identity=_snapshot("u1", 1),
            execution_result_ref=_ref("exec"),
        )
        assert outcome.identity.unit_id == "u1"


class TestAttemptRecord:
    def test_valid(self):
        record = AttemptRecord(
            identity=_snapshot(),
            experiment_plan_ref=_ref("plan"),
            outcome=AttemptOutcome(
                identity=_snapshot(),
                execution_result_ref=_ref("exec"),
            ),
            resource_usage_ref=_ref("usage"),
        )
        assert record.identity.attempt_number == 1


class TestResolvedArtifactBinding:
    def test_valid(self):
        b = ResolvedArtifactBinding(role="metrics", resolved_ref=_ref())
        assert b.role == "metrics"


class TestProducedArtifactRecord:
    def test_valid(self):
        record = ProducedArtifactRecord(
            unit_id="u1",
            attempt_identity=_snapshot(),
            bindings=[ResolvedArtifactBinding(role="metrics", resolved_ref=_ref())],
        )
        assert len(record.bindings) == 1


class TestResourceUsageReport:
    def test_valid(self):
        r = ResourceUsageReport(gpu_count_used=1, wall_time_seconds=3600)
        assert r.gpu_count_used == 1
        assert r.wall_time_seconds == 3600.0

    def test_actual_gpu_hours_computed(self):
        r = ResourceUsageReport(gpu_count_used=2, wall_time_seconds=7200)
        assert r.actual_gpu_hours == 4.0  # 2 * 7200 / 3600

    def test_actual_gpu_hours_zero_gpu(self):
        r = ResourceUsageReport(gpu_count_used=0, wall_time_seconds=3600)
        assert r.actual_gpu_hours == 0.0

    def test_gpu_count_ge_0(self):
        with pytest.raises(Exception):
            ResourceUsageReport(gpu_count_used=-1, wall_time_seconds=3600)

    def test_wall_time_ge_0(self):
        with pytest.raises(Exception):
            ResourceUsageReport(gpu_count_used=1, wall_time_seconds=-1)

    def test_memory_peak_optional(self):
        r = ResourceUsageReport(gpu_count_used=1, wall_time_seconds=100)
        assert r.memory_peak_bytes is None

    def test_memory_peak_ge_0(self):
        with pytest.raises(Exception):
            ResourceUsageReport(
                gpu_count_used=1, wall_time_seconds=100, memory_peak_bytes=-1
            )


class TestRetryIdentity:
    def test_valid(self):
        ri = RetryIdentity(unit_id="u1", attempt_number=1, retry_reason="timeout")
        assert ri.retry_reason == "timeout"


class TestRetryDecision:
    def test_valid_retry(self):
        rd = RetryDecision(
            identity=RetryIdentity(unit_id="u1", attempt_number=1, retry_reason="timeout"),
            should_retry=True,
            reason="will increase timeout",
            next_attempt_number=2,
            failure_classification="environment",
        )
        assert rd.should_retry is True

    def test_valid_no_retry(self):
        rd = RetryDecision(
            identity=RetryIdentity(unit_id="u1", attempt_number=1, retry_reason="terminal"),
            should_retry=False,
            reason="terminal failure",
            next_attempt_number=1,
            failure_classification="metric",
        )
        assert rd.should_retry is False

    def test_retry_next_must_be_greater(self):
        with pytest.raises(Exception, match="next_attempt_number"):
            RetryDecision(
                identity=RetryIdentity(unit_id="u1", attempt_number=1, retry_reason="x"),
                should_retry=True,
                reason="x",
                next_attempt_number=1,
                failure_classification="metric",
            )

    def test_no_retry_next_must_equal(self):
        with pytest.raises(Exception, match="next_attempt_number"):
            RetryDecision(
                identity=RetryIdentity(unit_id="u1", attempt_number=1, retry_reason="x"),
                should_retry=False,
                reason="x",
                next_attempt_number=2,
                failure_classification="metric",
            )


class TestExecutionUnitRecord:
    def test_valid_no_attempts(self):
        record = ExecutionUnitRecord(
            plan=ExecutionUnitPlan(
                unit_id="u1", workspace_id="ws_01", command_plan="x",
                max_wall_time_seconds=3600,
            ),
            final_status="pending",
        )
        assert record.final_status == "pending"

    def test_unit_attempt_consistency(self):
        plan = ExecutionUnitPlan(
            unit_id="u1", workspace_id="ws_01", command_plan="x",
            max_wall_time_seconds=3600,
        )
        record = ExecutionUnitRecord(
            plan=plan,
            attempts=[
                AttemptRecord(
                    identity=_snapshot("u1", 1),
                    experiment_plan_ref=_ref("plan"),
                    outcome=AttemptOutcome(
                        identity=_snapshot("u1", 1),
                        execution_result_ref=_ref("exec"),
                    ),
                    resource_usage_ref=_ref("usage"),
                )
            ],
            final_status="succeeded",
        )
        assert record.final_status == "succeeded"

    def test_unit_attempt_mismatch(self):
        plan = ExecutionUnitPlan(
            unit_id="u1", workspace_id="ws_01", command_plan="x",
            max_wall_time_seconds=3600,
        )
        with pytest.raises(Exception, match="unit_id"):
            ExecutionUnitRecord(
                plan=plan,
                attempts=[
                    AttemptRecord(
                        identity=_snapshot("u2", 1),
                        experiment_plan_ref=_ref("plan"),
                        outcome=AttemptOutcome(
                            identity=_snapshot("u2", 1),
                            execution_result_ref=_ref("exec"),
                        ),
                        resource_usage_ref=_ref("usage"),
                    )
                ],
                final_status="failed",
            )


class TestExecutionManifest:
    def test_minimal(self):
        m = ExecutionManifest(run_id="run_test", overall_status="pending")
        assert m.schema_version == 1
        assert m.unit_records == []

    def test_with_records(self):
        m = ExecutionManifest(
            run_id="run_test",
            unit_records=[
                ExecutionUnitRecord(
                    plan=ExecutionUnitPlan(
                        unit_id="u1", workspace_id="ws_01", command_plan="x",
                        max_wall_time_seconds=3600,
                    ),
                    final_status="succeeded",
                )
            ],
            overall_status="succeeded",
        )
        assert len(m.unit_records) == 1


class TestExperimentExecutionHandoff:
    def test_valid(self):
        snap = _snapshot("u1", 1)
        unit_record = ExecutionUnitRecord(
            plan=ExecutionUnitPlan(
                unit_id="u1", workspace_id="ws_01", command_plan="x",
                max_wall_time_seconds=3600,
            ),
            attempts=[
                AttemptRecord(
                    identity=snap,
                    experiment_plan_ref=_ref("plan"),
                    outcome=AttemptOutcome(
                        identity=snap, execution_result_ref=_ref("exec"),
                    ),
                    resource_usage_ref=_ref("usage"),
                )
            ],
            final_status="succeeded",
        )
        handoff = ExperimentExecutionHandoff(
            manifest=ExecutionManifest(
                run_id="run_test",
                unit_records=[unit_record],
                overall_status="succeeded",
            ),
            identity_snapshots=[snap],
        )
        assert len(handoff.identity_snapshots) == 1

    def test_missing_snapshot(self):
        snap = _snapshot("u1", 1)
        unit_record = ExecutionUnitRecord(
            plan=ExecutionUnitPlan(
                unit_id="u1", workspace_id="ws_01", command_plan="x",
                max_wall_time_seconds=3600,
            ),
            attempts=[
                AttemptRecord(
                    identity=snap,
                    experiment_plan_ref=_ref("plan"),
                    outcome=AttemptOutcome(
                        identity=snap, execution_result_ref=_ref("exec"),
                    ),
                    resource_usage_ref=_ref("usage"),
                )
            ],
            final_status="succeeded",
        )
        with pytest.raises(Exception, match="missing from identity_snapshots"):
            ExperimentExecutionHandoff(
                manifest=ExecutionManifest(
                    run_id="run_test",
                    unit_records=[unit_record],
                    overall_status="succeeded",
                ),
                identity_snapshots=[],
            )


class TestExecutionUnitResourceLedger:
    def test_valid(self):
        ledger = ExecutionUnitResourceLedger(
            unit_id="u1",
            resource_reports=[_ref("r1")],
            total_wall_time=3600.0,
            total_gpu_hours=2.0,
        )
        assert ledger.total_gpu_hours == 2.0

    def test_total_wall_time_ge_0(self):
        with pytest.raises(Exception):
            ExecutionUnitResourceLedger(
                unit_id="u1", total_wall_time=-1, total_gpu_hours=0,
            )


class TestResolvedArtifact:
    def test_valid(self):
        ra = ResolvedArtifact[int](
            artifact_id="a1",
            artifact_ref=_ref("a1"),
            payload=42,
        )
        assert ra.payload == 42

    def test_str_payload(self):
        ra = ResolvedArtifact[str](
            artifact_id="a1",
            artifact_ref=_ref("a1"),
            payload="hello",
        )
        assert ra.payload == "hello"
