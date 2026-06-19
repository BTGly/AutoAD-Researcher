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
    canonical_sha,
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


def _artifact_refs(result: PatchExecutionResult):
    """Build real artifact refs from a PatchExecutionResult.

    Computes canonical SHAs from the actual manifest and validation report
    objects so the bridge can validate ref → object consistency.
    """
    manifest = result.manifests[0]
    matching = [r for r in result.validation_reports if r.workspace_id == manifest.workspace_id]
    report = matching[0]

    manifest_sha = canonical_sha(manifest)
    report_sha = canonical_sha(report)

    return dict(
        baseline_repository_validation_ref=ArtifactReferenceV2(
            artifact_id="val_ws_baseline",
            artifact_type="repository_validation",
            locator="runs/run_test/ws_baseline/validation.json",
            sha256=report_sha,
        ),
        patch_application_manifest_ref=ArtifactReferenceV2(
            artifact_id="manifest_run_test_ws",
            artifact_type="patch_application_manifest",
            locator="runs/run_test/ws/manifest.json",
            sha256=manifest_sha,
        ),
        post_patch_validation_report_ref=ArtifactReferenceV2(
            artifact_id="post_val_run_test_ws",
            artifact_type="post_patch_validation_report",
            locator="runs/run_test/ws/post_validation.json",
            sha256=report_sha,
        ),
    )


def _handoff_args(**kw):
    """Build the common kwargs dict for build_patch_runner_handoff."""
    plan = kw.get("plan", _plan())
    result = kw.get("result", _result())
    refs = _artifact_refs(result)
    return dict(
        run_id="run_test",
        patch_execution_result=result,
        plan=plan,
        repository_before_commit=_HEX40,
        experiment_bundle_ref="bundle_001",
        baseline_repository_validation_ref=refs["baseline_repository_validation_ref"],
        patch_application_manifest_ref=refs["patch_application_manifest_ref"],
        post_patch_validation_report_ref=refs["post_patch_validation_report_ref"],
    )


# ── build_patch_runner_handoff ────────────────────────────────────────


class TestBuildPatchRunnerHandoff:
    def test_builds_valid_handoff(self):
        handoff = build_patch_runner_handoff(**_handoff_args())
        assert handoff.status == "eligible_for_runner_intake"
        assert handoff.run_id == "run_test"
        assert "v1" in handoff.selected_variant_ids

    def test_raises_on_not_eligible(self):
        result = _result(next_stage="replan_required")
        with pytest.raises(ValueError, match="not eligible for intake"):
            build_patch_runner_handoff(**_handoff_args(result=result))

    def test_raises_on_missing_manifest(self):
        result = PatchExecutionResult(
            result_id="result_run_test", run_id="run_test",
            overall_status="patch_applied_and_local_validations_passed",
            next_stage="eligible_for_runner_intake",
            manifests=[],
            validation_reports=[_PostPatchValidationReport()],
        )
        with pytest.raises(ValueError, match="has no manifests"):
            build_patch_runner_handoff(
                run_id="run_test", patch_execution_result=result,
                plan=_plan(), repository_before_commit=_HEX40,
                experiment_bundle_ref="bundle_001",
                baseline_repository_validation_ref=ArtifactReferenceV2(
                    artifact_id="val_ws_baseline", artifact_type="repository_validation",
                    locator="runs/run_test/ws_baseline/validation.json",
                    sha256="a" * 64,
                ),
                patch_application_manifest_ref=ArtifactReferenceV2(
                    artifact_id="manifest_run_test_ws", artifact_type="patch_application_manifest",
                    locator="runs/run_test/ws/manifest.json",
                    sha256="b" * 64,
                ),
                post_patch_validation_report_ref=ArtifactReferenceV2(
                    artifact_id="post_val_run_test_ws", artifact_type="post_patch_validation_report",
                    locator="runs/run_test/ws/post_validation.json",
                    sha256="c" * 64,
                ),
            )

    def test_handoff_round_trips_through_validators(self):
        handoff = build_patch_runner_handoff(**_handoff_args())
        assert handoff.next_stage == "runner_intake"

    def test_baseline_workspace_ref(self):
        handoff = build_patch_runner_handoff(**_handoff_args())
        ref = handoff.baseline_workspace_ref
        assert ref.repository_fingerprint == "f" * 64
        # Must not be placeholder SHA
        assert ref.repository_validation_ref.sha256 != "0" * 64

    def test_manifest_ref_sha_matches_canonical(self):
        result = _result()
        manifest = result.manifests[0]
        expected = canonical_sha(manifest)
        args = _handoff_args(result=result)
        handoff = build_patch_runner_handoff(**args)
        assert handoff.variant_workspaces[0].patch_application_manifest_ref.sha256 == expected

    def test_validation_ref_sha_matches_canonical(self):
        result = _result()
        report = result.validation_reports[0]
        expected = canonical_sha(report)
        args = _handoff_args(result=result)
        handoff = build_patch_runner_handoff(**args)
        assert handoff.variant_workspaces[0].post_patch_validation_report_ref.sha256 == expected

    def test_local_validation_report_sha_matches_validation_ref(self):
        handoff = build_patch_runner_handoff(**_handoff_args())
        vw = handoff.variant_workspaces[0]
        assert vw.local_validation_report_sha256 == vw.post_patch_validation_report_ref.sha256

    def test_raises_on_wrong_manifest_sha(self):
        result = _result()
        refs = _artifact_refs(result)
        # Corrupt manifest ref SHA
        refs["patch_application_manifest_ref"].sha256 = "d" * 64
        with pytest.raises(ValueError, match="does not match canonical_sha"):
            build_patch_runner_handoff(
                run_id="run_test", patch_execution_result=result,
                plan=_plan(), repository_before_commit=_HEX40,
                experiment_bundle_ref="bundle_001",
                **refs,
            )

    def test_raises_on_wrong_validation_report_sha(self):
        result = _result()
        refs = _artifact_refs(result)
        refs["post_patch_validation_report_ref"].sha256 = "d" * 64
        with pytest.raises(ValueError, match="does not match canonical_sha"):
            build_patch_runner_handoff(
                run_id="run_test", patch_execution_result=result,
                plan=_plan(), repository_before_commit=_HEX40,
                experiment_bundle_ref="bundle_001",
                **refs,
            )

    def test_raises_on_placeholder_baseline_sha(self):
        result = _result()
        refs = _artifact_refs(result)
        refs["baseline_repository_validation_ref"].sha256 = "0" * 64
        with pytest.raises(ValueError, match="placeholder"):
            build_patch_runner_handoff(
                run_id="run_test", patch_execution_result=result,
                plan=_plan(), repository_before_commit=_HEX40,
                experiment_bundle_ref="bundle_001",
                **refs,
            )

    def test_raises_on_no_matching_validation_report(self):
        result = _result()
        refs = _artifact_refs(result)
        # Mutate AFTER computing ref SHAs to break bridge's lookup
        result.validation_reports[0].workspace_id = "other_ws"
        with pytest.raises(ValueError, match="no PostPatchValidationReport found"):
            build_patch_runner_handoff(
                run_id="run_test", patch_execution_result=result,
                plan=_plan(), repository_before_commit=_HEX40,
                experiment_bundle_ref="bundle_001",
                **refs,
            )


# ── build_runner_intake_request ───────────────────────────────────────


class TestBuildRunnerIntakeRequest:
    def test_builds_with_derived_workspace_refs(self):
        handoff = build_patch_runner_handoff(**_handoff_args())
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
        handoff = build_patch_runner_handoff(**_handoff_args())
        intake = build_runner_intake_request(
            handoff=handoff,
            handoff_artifact_sha256=_SHA,
            experiment_planner_handoff_sha256=_SHA,
            experiment_matrix_sha256=_SHA,
            shared_protocol_fingerprint="fp_001",
            statistical_analysis_plan_sha256=_SHA,
            operational_guard_policy_sha256=_SHA,
        )
        assert intake.patch_runner_handoff_ref is not None
