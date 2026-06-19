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
    PostPatchValidationReport,
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


def _CheckResult(**kw):
    from autoad_researcher.schemas.patch_planning import CheckResult
    return CheckResult(status=kw.get("status", "passed"))


def _make_report(workspace_id="ws", report_id="pvr_ws"):
    return PostPatchValidationReport(
        report_id=report_id,
        run_id="run_test",
        workspace_id=workspace_id,
        manifest_id=f"manifest_{workspace_id}",
        status="patch_applied_and_local_validations_passed",
        syntax_check=_CheckResult(),
        format_check=_CheckResult(),
        static_check=_CheckResult(),
        type_check=_CheckResult(),
        import_check=_CheckResult(),
        validated_at="2026-06-18T00:00:00Z",
    )


def _make_manifest(workspace_id="ws", manifest_id=None):
    return PatchApplicationManifest(
        manifest_id=manifest_id or f"manifest_{workspace_id}",
        run_id="run_test",
        workspace_id=workspace_id,
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
    manifest = kw.get("manifest", _make_manifest(workspace_id="ws"))
    report = kw.get("report", _make_report(workspace_id="ws"))
    return PatchExecutionResult(
        result_id="result_run_test",
        run_id="run_test",
        overall_status=kw.get("status", "patch_applied_and_local_validations_passed"),
        next_stage=kw.get("next_stage", "eligible_for_runner_intake"),
        manifests=[manifest],
        validation_reports=[report],
    )


def _artifact_refs(result: PatchExecutionResult):
    """Build real artifact refs from a PatchExecutionResult.

    Computes canonical SHAs from the actual manifest and validation report
    objects so the bridge can validate ref → object consistency.

    Also builds a separate baseline validation report for the baseline
    workspace.
    """
    baseline_report = _make_report(workspace_id="ws_baseline", report_id="pvr_baseline")
    baseline_sha = canonical_sha(baseline_report)

    manifest = result.manifests[0]
    report = result.validation_reports[0]
    manifest_sha = canonical_sha(manifest)
    report_sha = canonical_sha(report)

    return dict(
        baseline_repository_validation_ref=ArtifactReferenceV2(
            artifact_id="val_ws_baseline",
            artifact_type="repository_validation",
            locator="runs/run_test/ws_baseline/validation.json",
            sha256=baseline_sha,
        ),
        baseline_validation_report=baseline_report,
        patch_application_manifest_refs_by_workspace={
            manifest.workspace_id: ArtifactReferenceV2(
                artifact_id=f"manifest_{manifest.workspace_id}",
                artifact_type="patch_application_manifest",
                locator=f"runs/run_test/{manifest.workspace_id}/manifest.json",
                sha256=manifest_sha,
            ),
        },
        post_patch_validation_report_refs_by_workspace={
            report.workspace_id: ArtifactReferenceV2(
                artifact_id=f"post_val_{report.workspace_id}",
                artifact_type="post_patch_validation_report",
                locator=f"runs/run_test/{report.workspace_id}/post_validation.json",
                sha256=report_sha,
            ),
        },
    )


def _default_kwargs(result=None, plan=None, **overrides):
    """Build default kwargs dict for build_patch_runner_handoff.

    ``overrides`` are merged on top of the computed refs so callers can
    corrupt individual ref SHAs or swap entire dicts for negative tests.
    """
    result = result or _result()
    plan = plan or _plan()
    refs = _artifact_refs(result)
    kwargs = dict(
        run_id="run_test",
        patch_execution_result=result,
        plan=plan,
        repository_before_commit=_HEX40,
        experiment_bundle_ref="bundle_001",
        baseline_repository_validation_ref=refs["baseline_repository_validation_ref"],
        baseline_validation_report=refs["baseline_validation_report"],
        patch_application_manifest_refs_by_workspace=refs["patch_application_manifest_refs_by_workspace"],
        post_patch_validation_report_refs_by_workspace=refs["post_patch_validation_report_refs_by_workspace"],
    )
    kwargs.update(overrides)
    return kwargs


# ── build_patch_runner_handoff ────────────────────────────────────────


class TestBuildPatchRunnerHandoff:
    def test_builds_valid_handoff(self):
        handoff = build_patch_runner_handoff(**_default_kwargs())
        assert handoff.status == "eligible_for_runner_intake"
        assert handoff.run_id == "run_test"
        assert "v1" in handoff.selected_variant_ids

    def test_raises_on_not_eligible(self):
        result = _result(next_stage="replan_required")
        with pytest.raises(ValueError, match="not eligible for intake"):
            build_patch_runner_handoff(**_default_kwargs(result=result))

    def test_raises_on_missing_manifest(self):
        result = PatchExecutionResult(
            result_id="result_run_test", run_id="run_test",
            overall_status="patch_applied_and_local_validations_passed",
            next_stage="eligible_for_runner_intake",
            manifests=[], validation_reports=[_make_report(workspace_id="ws")],
        )
        baseline_report = _make_report(workspace_id="ws_baseline", report_id="pvr_baseline")
        baseline_sha = canonical_sha(baseline_report)
        with pytest.raises(ValueError, match="no PatchApplicationManifest found"):
            build_patch_runner_handoff(
                run_id="run_test", patch_execution_result=result,
                plan=_plan(), repository_before_commit=_HEX40,
                experiment_bundle_ref="bundle_001",
                baseline_repository_validation_ref=ArtifactReferenceV2(
                    artifact_id="val_ws_baseline", artifact_type="repository_validation",
                    locator="runs/run_test/ws_baseline/validation.json",
                    sha256=baseline_sha,
                ),
                baseline_validation_report=baseline_report,
                patch_application_manifest_refs_by_workspace={
                    "ws": ArtifactReferenceV2(
                        artifact_id="manifest_ws", artifact_type="patch_application_manifest",
                        locator="runs/run_test/ws/manifest.json",
                        sha256="a" * 64,
                    ),
                },
                post_patch_validation_report_refs_by_workspace={
                    "ws": ArtifactReferenceV2(
                        artifact_id="post_val_ws", artifact_type="post_patch_validation_report",
                        locator="runs/run_test/ws/post_validation.json",
                        sha256="b" * 64,
                    ),
                },
            )

    def test_handoff_round_trips_through_validators(self):
        handoff = build_patch_runner_handoff(**_default_kwargs())
        assert handoff.next_stage == "runner_intake"

    def test_baseline_workspace_ref(self):
        handoff = build_patch_runner_handoff(**_default_kwargs())
        ref = handoff.baseline_workspace_ref
        assert ref.repository_fingerprint == "f" * 64
        assert ref.repository_validation_ref.sha256 != "0" * 64

    def test_manifest_ref_sha_matches_canonical(self):
        handoff = build_patch_runner_handoff(**_default_kwargs())
        vw = handoff.variant_workspaces[0]
        assert vw.workspace_id == "ws"
        expected = canonical_sha(_make_manifest(workspace_id="ws"))
        assert vw.patch_application_manifest_ref.sha256 == expected

    def test_validation_ref_sha_matches_canonical(self):
        handoff = build_patch_runner_handoff(**_default_kwargs())
        vw = handoff.variant_workspaces[0]
        assert vw.workspace_id == "ws"
        expected = canonical_sha(_make_report(workspace_id="ws"))
        assert vw.post_patch_validation_report_ref.sha256 == expected

    def test_local_validation_report_sha_matches_validation_ref(self):
        handoff = build_patch_runner_handoff(**_default_kwargs())
        vw = handoff.variant_workspaces[0]
        assert vw.local_validation_report_sha256 == vw.post_patch_validation_report_ref.sha256

    def test_raises_on_wrong_manifest_sha(self):
        result = _result()
        refs = _artifact_refs(result)
        ws = "ws"
        refs["patch_application_manifest_refs_by_workspace"][ws].sha256 = "d" * 64
        with pytest.raises(ValueError, match="does not match canonical_sha"):
            build_patch_runner_handoff(**_default_kwargs(result=result, **refs))

    def test_raises_on_wrong_validation_report_sha(self):
        result = _result()
        refs = _artifact_refs(result)
        ws = "ws"
        refs["post_patch_validation_report_refs_by_workspace"][ws].sha256 = "d" * 64
        with pytest.raises(ValueError, match="does not match canonical_sha"):
            build_patch_runner_handoff(**_default_kwargs(result=result, **refs))

    def test_raises_on_wrong_baseline_sha(self):
        result = _result()
        refs = _artifact_refs(result)
        refs["baseline_repository_validation_ref"].sha256 = "e" * 64
        with pytest.raises(ValueError, match="does not match canonical_sha"):
            build_patch_runner_handoff(**_default_kwargs(result=result, **refs))

    def test_raises_on_placeholder_baseline_sha(self):
        result = _result()
        refs = _artifact_refs(result)
        refs["baseline_repository_validation_ref"].sha256 = "0" * 64
        with pytest.raises(ValueError, match="does not match canonical_sha"):
            build_patch_runner_handoff(**_default_kwargs(result=result, **refs))

    def test_raises_on_missing_workspace_in_refs_dicts(self):
        result = _result()
        refs = _artifact_refs(result)
        refs["patch_application_manifest_refs_by_workspace"] = {}
        with pytest.raises(ValueError, match="no patch_application_manifest_ref"):
            build_patch_runner_handoff(**_default_kwargs(result=result, **refs))

    def test_raises_on_missing_workspace_in_report_refs_dict(self):
        result = _result()
        refs = _artifact_refs(result)
        refs["post_patch_validation_report_refs_by_workspace"] = {}
        with pytest.raises(ValueError, match="no post_patch_validation_report_ref"):
            build_patch_runner_handoff(**_default_kwargs(result=result, **refs))

    def test_raises_on_no_matching_manifest(self):
        """Bridge raises when the result lacks a manifest for the variant workspace.

        Refs are properly provided for ``ws`` (the variant workspace from the
        plan), but the result only contains a manifest for ``unused_ws``.
        """
        result = PatchExecutionResult(
            result_id="result_run_test", run_id="run_test",
            overall_status="patch_applied_and_local_validations_passed",
            next_stage="eligible_for_runner_intake",
            manifests=[_make_manifest(workspace_id="unused_ws")],
            validation_reports=[_make_report(workspace_id="ws")],
        )
        baseline_report = _make_report(workspace_id="ws_baseline", report_id="pvr_baseline")
        baseline_sha = canonical_sha(baseline_report)
        report_sha = canonical_sha(_make_report(workspace_id="ws"))
        with pytest.raises(ValueError, match="no PatchApplicationManifest found"):
            build_patch_runner_handoff(
                run_id="run_test", patch_execution_result=result,
                plan=_plan(), repository_before_commit=_HEX40,
                experiment_bundle_ref="bundle_001",
                baseline_repository_validation_ref=ArtifactReferenceV2(
                    artifact_id="val_ws_baseline", artifact_type="repository_validation",
                    locator="runs/run_test/ws_baseline/validation.json",
                    sha256=baseline_sha,
                ),
                baseline_validation_report=baseline_report,
                patch_application_manifest_refs_by_workspace={
                    "ws": ArtifactReferenceV2(
                        artifact_id="manifest_ws", artifact_type="patch_application_manifest",
                        locator="runs/run_test/ws/manifest.json",
                        sha256="a" * 64,
                    ),
                },
                post_patch_validation_report_refs_by_workspace={
                    "ws": ArtifactReferenceV2(
                        artifact_id="post_val_ws", artifact_type="post_patch_validation_report",
                        locator="runs/run_test/ws/post_validation.json",
                        sha256=report_sha,
                    ),
                },
            )

    def test_raises_on_no_matching_validation_report(self):
        result = _result()
        refs = _artifact_refs(result)
        result.validation_reports[0].workspace_id = "other_ws"
        with pytest.raises(ValueError, match="no PostPatchValidationReport found"):
            build_patch_runner_handoff(**_default_kwargs(result=result, **refs))

    def test_raises_on_wrong_manifest_workspace(self):
        """Manifest workspace_id must match the variant workspace in the plan.

        Refs are keyed by ``ws`` but the only manifest in the result has
        workspace_id ``wrong_ws``, so the per-workspace lookup fails.
        """
        result = PatchExecutionResult(
            result_id="result_run_test", run_id="run_test",
            overall_status="patch_applied_and_local_validations_passed",
            next_stage="eligible_for_runner_intake",
            manifests=[_make_manifest(workspace_id="wrong_ws")],
            validation_reports=[_make_report(workspace_id="ws")],
        )
        baseline_report = _make_report(workspace_id="ws_baseline", report_id="pvr_baseline")
        baseline_sha = canonical_sha(baseline_report)
        report_sha = canonical_sha(_make_report(workspace_id="ws"))
        with pytest.raises(ValueError, match="no PatchApplicationManifest found"):
            build_patch_runner_handoff(
                run_id="run_test", patch_execution_result=result,
                plan=_plan(), repository_before_commit=_HEX40,
                experiment_bundle_ref="bundle_001",
                baseline_repository_validation_ref=ArtifactReferenceV2(
                    artifact_id="val_ws_baseline", artifact_type="repository_validation",
                    locator="runs/run_test/ws_baseline/validation.json",
                    sha256=baseline_sha,
                ),
                baseline_validation_report=baseline_report,
                patch_application_manifest_refs_by_workspace={
                    "ws": ArtifactReferenceV2(
                        artifact_id="manifest_ws", artifact_type="patch_application_manifest",
                        locator="runs/run_test/ws/manifest.json",
                        sha256="a" * 64,
                    ),
                },
                post_patch_validation_report_refs_by_workspace={
                    "ws": ArtifactReferenceV2(
                        artifact_id="post_val_ws", artifact_type="post_patch_validation_report",
                        locator="runs/run_test/ws/post_validation.json",
                        sha256=report_sha,
                    ),
                },
            )


# ── build_runner_intake_request ───────────────────────────────────────


class TestBuildRunnerIntakeRequest:
    def test_builds_with_derived_workspace_refs(self):
        handoff = build_patch_runner_handoff(**_default_kwargs())
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
        handoff = build_patch_runner_handoff(**_default_kwargs())
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
