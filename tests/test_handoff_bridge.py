"""Tests for 3.7→3.8 handoff bridge."""

import pytest

from autoad_researcher.runner.handoff_bridge import (
    build_patch_runner_handoff,
    build_runner_intake_request,
)
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.execution import RunnerIntakeRequest, WorkspaceExecutionRef
from autoad_researcher.schemas.patch_planning import (
    PatchApplicationManifest,
    PatchExecutionResult,
    PatchRunnerHandoff,
    RepositoryChangePlan,
    VariantWorkspacePlan,
)

# ── Constants ─────────────────────────────────────────────────────────

_SHA = "c" * 64
_HEX40 = "a" * 40


# ── Fixtures ──────────────────────────────────────────────────────────


def _plan(**kw):
    from autoad_researcher.schemas.patch_planning import compute_canonical_plan_sha256
    ws = kw.get("ws", "ws")
    baseline_wp = VariantWorkspacePlan(
        workspace_id="ws_baseline", variant_ids=[],
        isolation_mode="shared_workspace",
        base_repository_source_id="src_test", base_commit=_HEX40,
        planned_change_ids=["chg_1"],
    )
    variant_wp = VariantWorkspacePlan(
        workspace_id=ws, variant_ids=["v1"],
        isolation_mode="shared_workspace",
        base_repository_source_id="src_test", base_commit=_HEX40,
        planned_change_ids=["chg_1"],
    )
    plan = RepositoryChangePlan(
        schema_version=2, run_id="run_test",
        patch_plan_id="plan_test",
        repository_source_id="src_test", repository_commit=_HEX40,
        repository_fingerprint="f" * 64,
        idea_id="idea_test",
        changes=[], workspace_plans=[baseline_wp, variant_wp],
        patch_plan_sha256=_SHA,
    )
    return plan


def _manifest(run_id="run_test"):
    return PatchApplicationManifest(
        manifest_id=f"manifest_{run_id}_ws",
        run_id=run_id, workspace_id="ws",
        approved_decision_id="dec_1",
        repository_before_fingerprint="f" * 64,
        repository_after_fingerprint="f" * 64,
        attempted_change_ids=["chg_1"],
        applied_change_ids=["chg_1"],
        skipped_change_ids=[], failed_changes=[],
        changed_files=[], patch_diff_sha256=_SHA,
        applied_at="2026-06-18T00:00:00Z",
    )


def _result(**kw):
    return PatchExecutionResult(
        result_id="result_run_test",
        run_id="run_test",
        overall_status=kw.get("status", "patch_applied_and_local_validations_passed"),
        next_stage=kw.get("next_stage", "eligible_for_runner_intake"),
        manifests=[kw.get("manifest", _manifest())],
        validation_reports=[_PostPatchValidationReport()],
    )


def _PostPatchValidationReport():
    from autoad_researcher.schemas.patch_planning import CheckResult, PostPatchValidationReport
    return PostPatchValidationReport(
        report_id="pvr_run_test",
        run_id="run_test",
        workspace_id="ws",
        manifest_id="manifest_run_test_ws",
        status="patch_applied_and_local_validations_passed",
        syntax_check=CheckResult(status="passed"),
        format_check=CheckResult(status="passed"),
        static_check=CheckResult(status="passed"),
        type_check=CheckResult(status="passed"),
        import_check=CheckResult(status="passed"),
        validated_at="2026-06-18T00:00:00Z",
    )


# ── build_patch_runner_handoff ────────────────────────────────────────


class TestBuildPatchRunnerHandoff:
    def test_builds_valid_handoff(self):
        plan = _plan()
        result = _result()
        handoff = build_patch_runner_handoff(
            run_id="run_test",
            patch_execution_result=result,
            plan=plan,
            repository_before_commit=_HEX40,
            experiment_bundle_ref="bundle_001",
        )
        assert handoff.status == "eligible_for_runner_intake"
        assert handoff.run_id == "run_test"
        assert "v1" in handoff.selected_variant_ids

    def test_raises_on_not_eligible(self):
        plan = _plan()
        result = _result(next_stage="replan_required")
        with pytest.raises(ValueError, match="not eligible for intake"):
            build_patch_runner_handoff(
                run_id="run_test",
                patch_execution_result=result,
                plan=plan,
                repository_before_commit=_HEX40,
                experiment_bundle_ref="bundle_001",
            )

    def test_raises_on_missing_manifest(self):
        plan = _plan()
        result = PatchExecutionResult(
            result_id="result_run_test", run_id="run_test",
            overall_status="patch_applied_and_local_validations_passed",
            next_stage="eligible_for_runner_intake",
            manifests=[],
            validation_reports=[_PostPatchValidationReport()],
        )
        with pytest.raises(ValueError, match="has no manifests"):
            build_patch_runner_handoff(
                run_id="run_test",
                patch_execution_result=result,
                plan=plan,
                repository_before_commit=_HEX40,
                experiment_bundle_ref="bundle_001",
            )

    def test_handoff_round_trips_through_validators(self):
        plan = _plan()
        result = _result()
        handoff = build_patch_runner_handoff(
            run_id="run_test",
            patch_execution_result=result,
            plan=plan,
            repository_before_commit=_HEX40,
            experiment_bundle_ref="bundle_001",
        )
        # PatchRunnerHandoff model validators run at construction
        assert handoff.next_stage == "runner_intake"

    def test_baseline_workspace_ref(self):
        plan = _plan()
        result = _result()
        handoff = build_patch_runner_handoff(
            run_id="run_test",
            patch_execution_result=result,
            plan=plan,
            repository_before_commit=_HEX40,
            experiment_bundle_ref="bundle_001",
        )
        assert handoff.baseline_workspace_ref.repository_fingerprint == "f" * 64
        assert handoff.baseline_workspace_ref.repository_validation_ref.sha256 == "0" * 64


# ── build_runner_intake_request ───────────────────────────────────────


class TestBuildRunnerIntakeRequest:
    def test_builds_with_derived_workspace_refs(self):
        plan = _plan()
        result = _result()
        handoff = build_patch_runner_handoff(
            run_id="run_test",
            patch_execution_result=result,
            plan=plan,
            repository_before_commit=_HEX40,
            experiment_bundle_ref="bundle_001",
        )
        intake = build_runner_intake_request(
            handoff=handoff,
            handoff_artifact_sha256=_SHA,
            experiment_planner_handoff_sha256=_SHA,
            experiment_matrix_sha256=_SHA,
            shared_protocol_fingerprint="fp_001",
            statistical_analysis_plan_sha256=_SHA,
            operational_guard_policy_sha256=_SHA,
        )
        assert len(intake.workspace_refs) >= 1
        assert intake.patch_runner_handoff_ref.sha256 == _SHA

    def test_intake_validation_passes(self):
        plan = _plan()
        result = _result()
        handoff = build_patch_runner_handoff(
            run_id="run_test",
            patch_execution_result=result,
            plan=plan,
            repository_before_commit=_HEX40,
            experiment_bundle_ref="bundle_001",
        )
        intake = build_runner_intake_request(
            handoff=handoff,
            handoff_artifact_sha256=_SHA,
            experiment_planner_handoff_sha256=_SHA,
            experiment_matrix_sha256=_SHA,
            shared_protocol_fingerprint="fp_001",
            statistical_analysis_plan_sha256=_SHA,
            operational_guard_policy_sha256=_SHA,
        )
        # Validates internally via validate_intake_against_patch_handoff
        assert intake.patch_runner_handoff_ref is not None
