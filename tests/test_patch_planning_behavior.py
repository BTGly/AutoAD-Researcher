"""Patch planning, approval, and controlled application behavior tests.

Covers:
  - PlannedRepositoryChange model validators (target_mode/change_kind)
  - RepositoryChangePlan construction
  - PatchConflictAnalyzer (clean, overlap, separate worktrees)
  - PatchPlanValidator (protected paths, hook refs, new target paths)
  - Approval protocol (consistency, approve_all semantics, policy deny)
  - ApprovalDecision model validators
  - Path derivation from change_ids
  - ControlledPatchApplicator (write gate, apply, rollback)
"""

from datetime import datetime, timezone

import pytest

from autoad_researcher.schemas.baseline_architecture import ModificationHook
from autoad_researcher.schemas.patch_planning import (
    ApprovalDecision,
    ApprovalRequest,
    PatchConflictAnalysis,
    PatchConflictGroup,
    PlannedRepositoryChange,
    RepositoryChangePlan,
    SymbolContractDelta,
    VariantWorkspacePlan,
    WorkspaceApprovalSummary,
)
from autoad_researcher.code_agent.approval import (
    compute_approval_effective_write_paths,
    validate_approved_paths_against_policy,
    validate_approval_consistency,
)
from autoad_researcher.code_agent.conflict_analyzer import analyze_variant_conflicts
from autoad_researcher.code_agent.patch_applicator import ControlledPatchApplicator
from autoad_researcher.code_agent.patch_planner import PatchPlannerAgent
from autoad_researcher.code_agent.planner_validator import validate_repository_change_plan
from autoad_researcher.schemas.transfer_design import (
    HookBinding,
    ImplementationVariant,
)

_NOW = datetime.now(timezone.utc)


def _make_plan(
    *,
    run_id: str = "run_test",
    changes: list[PlannedRepositoryChange] | None = None,
    deps: list | None = None,
    configs: list | None = None,
    tests: list | None = None,
) -> RepositoryChangePlan:
    return RepositoryChangePlan(
        run_id=run_id,
        patch_plan_id="pp_test",
        repository_source_id="src_test",
        repository_commit="a" * 40,
        repository_fingerprint="b" * 64,
        selected_variant_ids=[],
        idea_id="idea_test",
        changes=changes or [],
        dependency_changes=deps or [],
        configuration_changes=configs or [],
        test_changes=tests or [],
        plan_sha256="c" * 64,
    )


def _make_approval_request(
    *,
    plan_sha256: str = "c" * 64,
) -> ApprovalRequest:
    return ApprovalRequest(
        approval_request_id="ar_test",
        run_id="run_test",
        patch_plan_sha256=plan_sha256,
        repository_before_fingerprint="b" * 64,
        selected_variant_ids=[],
        overall_risk_level="low",
        workspace_summaries=[],
        dependency_changes_summary=[],
        validation_commands=[],
        created_at=_NOW,
    )


def _make_approval_decision(
    *,
    decision: str = "approve_all",
    approved_change_ids: list[str] | None = None,
    rejected_change_ids: list[str] | None = None,
    approved_paths: list[str] | None = None,
    plan_sha256: str = "c" * 64,
) -> ApprovalDecision:
    return ApprovalDecision(
        decision_id="ad_test",
        decision=decision,
        approved_patch_plan_sha256=plan_sha256,
        approved_change_ids=approved_change_ids or [],
        rejected_change_ids=rejected_change_ids or [],
        approved_dependency_change_ids=[],
        approved_validation_command_ids=[],
        approved_paths=approved_paths or [],
        user_evidence_id="ev_user_test",
        decided_at=_NOW,
    )


# ---------------------------------------------------------------------------
# T1: PlannedRepositoryChange model validators
# ---------------------------------------------------------------------------


class TestPlannedRepositoryChange:
    def test_create_must_be_new_target(self):
        with pytest.raises(ValueError, match="create requires target_mode=new_target"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="create",
                target_mode="existing_target",
                hook_id="hook_1",
                repository_path="src/new_file.py",
                variant_ids=["var_1"],
                rationale="test",
            )

    def test_delete_must_be_existing_target(self):
        with pytest.raises(ValueError, match="delete requires target_mode=existing_target"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="delete",
                target_mode="new_target",
                proposed_symbol="new_sym",
                repository_path="src/old_file.py",
                variant_ids=["var_1"],
                rationale="test",
            )

    def test_rename_must_be_existing_target(self):
        with pytest.raises(ValueError, match="rename requires target_mode=existing_target"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="rename",
                target_mode="new_target",
                proposed_symbol="new_sym",
                repository_path="src/old_file.py",
                variant_ids=["var_1"],
                rationale="test",
            )

    def test_modify_must_be_existing_target(self):
        with pytest.raises(ValueError, match="modify requires target_mode=existing_target"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="new_target",
                proposed_symbol="new_sym",
                repository_path="src/existing.py",
                variant_ids=["var_1"],
                rationale="test",
            )

    def test_existing_target_requires_hook_id_or_symbol_id(self):
        with pytest.raises(ValueError, match="existing_target requires hook_id or existing_symbol_id"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id=None,
                existing_symbol_id=None,
                repository_path="src/existing.py",
                variant_ids=["var_1"],
                rationale="test",
            )

    def test_new_target_must_not_set_hook_id(self):
        with pytest.raises(ValueError, match="new_target must not set hook_id"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="create",
                target_mode="new_target",
                hook_id="hook_1",
                proposed_symbol="NewClass",
                repository_path="src/new_file.py",
                variant_ids=["var_1"],
                rationale="test",
            )

    def test_new_target_requires_proposed_symbol(self):
        with pytest.raises(ValueError, match="new_target requires proposed_symbol"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="create",
                target_mode="new_target",
                proposed_symbol=None,
                repository_path="src/new_file.py",
                variant_ids=["var_1"],
                rationale="test",
            )

    def test_test_only_allows_both_target_modes(self):
        existing = PlannedRepositoryChange(
            change_id="chg_t1",
            workspace_id="ws_1",
            change_kind="test_only",
            target_mode="existing_target",
            hook_id="hook_test_1",
            repository_path="tests/test_existing.py",
            variant_ids=["var_1"],
            rationale="modify existing test",
        )
        assert existing.change_id == "chg_t1"

        new = PlannedRepositoryChange(
            change_id="chg_t2",
            workspace_id="ws_1",
            change_kind="test_only",
            target_mode="new_target",
            proposed_symbol="test_new_behavior",
            repository_path="tests/test_new.py",
            variant_ids=["var_1"],
            rationale="add new test",
        )
        assert new.change_id == "chg_t2"

    def test_configuration_only_allows_both_target_modes(self):
        existing = PlannedRepositoryChange(
            change_id="chg_c1",
            workspace_id="ws_1",
            change_kind="configuration_only",
            target_mode="existing_target",
            hook_id="hook_cfg_1",
            repository_path="configs/base.yaml",
            variant_ids=["var_1"],
            rationale="modify config",
        )
        assert existing.change_id == "chg_c1"

        new = PlannedRepositoryChange(
            change_id="chg_c2",
            workspace_id="ws_1",
            change_kind="configuration_only",
            target_mode="new_target",
            proposed_symbol="new_config",
            repository_path="configs/new.yaml",
            variant_ids=["var_1"],
            rationale="new config",
        )
        assert new.change_id == "chg_c2"


# ---------------------------------------------------------------------------
# T2: RepositoryChangePlan construction
# ---------------------------------------------------------------------------


class TestRepositoryChangePlan:
    def test_minimal_plan_round_trip(self):
        plan = _make_plan()
        assert plan.run_id == "run_test"
        assert plan.schema_version == 1
        assert plan.changes == []

    def test_plan_with_changes(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="modify",
            target_mode="existing_target",
            hook_id="hook_1",
            repository_path="src/model.py",
            variant_ids=["var_1"],
            rationale="test change",
        )
        plan = _make_plan(changes=[change])
        assert len(plan.changes) == 1
        assert plan.changes[0].change_id == "chg_001"


# ---------------------------------------------------------------------------
# T3: PatchConflictAnalyzer
# ---------------------------------------------------------------------------


class TestPatchConflictAnalyzer:
    def test_no_conflicts_shared_workspace(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_a",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h1",
                repository_path="src/a.py",
                variant_ids=["var_a"],
                rationale="A change",
            ),
            PlannedRepositoryChange(
                change_id="chg_b",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h2",
                repository_path="src/b.py",
                variant_ids=["var_b"],
                rationale="B change",
            ),
        ]

        result = analyze_variant_conflicts(
            changes=changes,
            variant_ids=["var_a", "var_b"],
            repository_source_id="src_test",
            repository_commit="a" * 40,
            run_id="run_test",
            analysis_id="analysis_1",
        )

        assert result.overall_status == "clean"
        assert len(result.workspace_plans) == 1
        assert result.workspace_plans[0].isolation_mode == "shared_workspace"

    def test_path_overlap_detected(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_a",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h1",
                repository_path="src/model.py",
                variant_ids=["var_a"],
                rationale="A change",
            ),
            PlannedRepositoryChange(
                change_id="chg_b",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h2",
                repository_path="src/model.py",
                variant_ids=["var_b"],
                rationale="B change",
            ),
        ]

        result = analyze_variant_conflicts(
            changes=changes,
            variant_ids=["var_a", "var_b"],
            repository_source_id="src_test",
            repository_commit="a" * 40,
            run_id="run_test",
            analysis_id="analysis_1",
        )

        assert result.overall_status == "worktree_split_required"
        assert len(result.conflict_groups) > 0
        assert result.conflict_groups[0].target_path == "src/model.py"

    def test_single_variant_no_conflict(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_a",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h1",
                repository_path="src/a.py",
                variant_ids=["var_a"],
                rationale="A change",
            ),
        ]

        result = analyze_variant_conflicts(
            changes=changes,
            variant_ids=["var_a"],
            repository_source_id="src_test",
            repository_commit="a" * 40,
            run_id="run_test",
            analysis_id="analysis_1",
        )

        assert result.overall_status == "clean"


# ---------------------------------------------------------------------------
# T4: PatchPlanValidator
# ---------------------------------------------------------------------------


class TestPatchPlanValidator:
    def test_passes_clean_plan(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="modify",
            target_mode="existing_target",
            hook_id="hook_1",
            repository_path="src/model.py",
            variant_ids=["var_1"],
            rationale="test",
        )
        plan = _make_plan(changes=[change])

        known_hooks = {
            "hook_1": ModificationHook(
                hook_id="hook_1",
                hook_name="model_forward",
                module_path="src/model.py",
                symbol="forward",
                semantic_role="entrypoint",
                path_classification="modifiable_candidate",
                allowed_for_transfer_design=True,
            )
        }

        report = validate_repository_change_plan(
            plan=plan,
            known_hooks=known_hooks,
            modifiable_paths={"src/model.py"},
            protected_paths=set(),
            report_id="report_1",
        )

        assert report.status == "passed"
        assert len(report.issues) == 0

    def test_flags_protected_path(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="modify",
            target_mode="existing_target",
            hook_id="hook_1",
            repository_path="eval/metrics.py",
            variant_ids=["var_1"],
            rationale="test",
        )
        plan = _make_plan(changes=[change])

        known_hooks = {
            "hook_1": ModificationHook(
                hook_id="hook_1",
                hook_name="eval_hook",
                module_path="eval/metrics.py",
                symbol="compute_metric",
                semantic_role="evaluation",
                path_classification="modifiable_candidate",
                allowed_for_transfer_design=True,
            )
        }

        report = validate_repository_change_plan(
            plan=plan,
            known_hooks=known_hooks,
            modifiable_paths=set(),
            protected_paths={"eval/metrics.py"},
            report_id="report_1",
        )

        assert report.status == "failed"
        assert any(i.category == "protected_path_violation" for i in report.issues)

    def test_flags_hook_classified_as_protected(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="modify",
            target_mode="existing_target",
            hook_id="hook_1",
            repository_path="src/model.py",
            variant_ids=["var_1"],
            rationale="test",
        )
        plan = _make_plan(changes=[change])

        known_hooks = {
            "hook_1": ModificationHook(
                hook_id="hook_1",
                hook_name="protected_hook",
                module_path="src/model.py",
                symbol="protected_fn",
                semantic_role="protected",
                path_classification="protected_candidate",
                allowed_for_transfer_design=False,
            )
        }

        report = validate_repository_change_plan(
            plan=plan,
            known_hooks=known_hooks,
            modifiable_paths={"src/model.py"},
            protected_paths=set(),
            report_id="report_1",
        )

        assert report.status == "failed"
        assert any(i.category == "path_classification_violation" for i in report.issues)

    def test_flags_hook_unknown_classification(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="modify",
            target_mode="existing_target",
            hook_id="hook_1",
            repository_path="src/model.py",
            variant_ids=["var_1"],
            rationale="test",
        )
        plan = _make_plan(changes=[change])

        known_hooks = {
            "hook_1": ModificationHook(
                hook_id="hook_1",
                hook_name="unknown_hook",
                module_path="src/model.py",
                symbol="unknown_fn",
                semantic_role="unknown",
                path_classification="unknown",
                allowed_for_transfer_design=False,
            )
        }

        report = validate_repository_change_plan(
            plan=plan,
            known_hooks=known_hooks,
            modifiable_paths={"src/model.py"},
            protected_paths=set(),
            report_id="report_1",
        )

        assert report.status == "failed"
        assert any("return_to_3_1" in i.resolution for i in report.issues)

    def test_flags_missing_hook_reference(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="modify",
            target_mode="existing_target",
            hook_id="nonexistent_hook",
            repository_path="src/model.py",
            variant_ids=["var_1"],
            rationale="test",
        )
        plan = _make_plan(changes=[change])

        report = validate_repository_change_plan(
            plan=plan,
            known_hooks={},
            modifiable_paths={"src/model.py"},
            protected_paths=set(),
            report_id="report_1",
        )

        assert report.status == "failed"
        assert any(i.category == "hook_reference_broken" for i in report.issues)

    def test_flags_new_target_in_protected_dir(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="create",
            target_mode="new_target",
            proposed_symbol="NewClass",
            repository_path="eval/new_metric.py",
            variant_ids=["var_1"],
            rationale="test",
        )
        plan = _make_plan(changes=[change])

        report = validate_repository_change_plan(
            plan=plan,
            known_hooks={},
            modifiable_paths=set(),
            protected_paths={"eval"},
            report_id="report_1",
        )

        assert report.status == "failed"
        assert any("protected_path_violation" in i.category for i in report.issues)

    def test_flags_system_path(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="create",
            target_mode="new_target",
            proposed_symbol="Bad",
            repository_path="/etc/my_config",
            variant_ids=["var_1"],
            rationale="bad",
        )
        plan = _make_plan(changes=[change])

        report = validate_repository_change_plan(
            plan=plan,
            known_hooks={},
            modifiable_paths=set(),
            protected_paths=set(),
            report_id="report_1",
        )

        assert report.status == "failed"
        assert any("path_classification_violation" in i.category for i in report.issues)


# ---------------------------------------------------------------------------
# T5: ApprovalDecision model validators
# ---------------------------------------------------------------------------


class TestApprovalDecision:
    def test_approve_all_requires_changes(self):
        with pytest.raises(ValueError, match="requires approved_change_ids"):
            ApprovalDecision(
                decision_id="ad_test",
                decision="approve_all",
                approved_patch_plan_sha256="a" * 64,
                approved_change_ids=[],
                approved_paths=[],
                user_evidence_id="ev_user",
                decided_at=_NOW,
            )

    def test_approve_partial_requires_changes(self):
        with pytest.raises(ValueError, match="requires approved_change_ids"):
            ApprovalDecision(
                decision_id="ad_test",
                decision="approve_partial",
                approved_patch_plan_sha256="a" * 64,
                approved_change_ids=[],
                approved_paths=[],
                user_evidence_id="ev_user",
                decided_at=_NOW,
            )

    def test_reject_must_not_approve(self):
        with pytest.raises(ValueError, match="must not have approved_change_ids"):
            ApprovalDecision(
                decision_id="ad_test",
                decision="reject",
                approved_patch_plan_sha256="a" * 64,
                approved_change_ids=["chg_1"],
                approved_paths=["src/a.py"],
                user_evidence_id="ev_user",
                decided_at=_NOW,
            )

    def test_revise_must_not_approve(self):
        with pytest.raises(ValueError, match="must not have approved_change_ids"):
            ApprovalDecision(
                decision_id="ad_test",
                decision="revise",
                approved_patch_plan_sha256="a" * 64,
                approved_change_ids=["chg_1"],
                approved_paths=["src/a.py"],
                user_evidence_id="ev_user",
                decided_at=_NOW,
            )

    def test_valid_approve_all(self):
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/a.py"],
        )
        assert decision.decision == "approve_all"
        assert decision.approved_change_ids == ["chg_1"]

    def test_valid_partial_approval(self):
        decision = _make_approval_decision(
            decision="approve_partial",
            approved_change_ids=["chg_1"],
            approved_paths=["src/a.py"],
            rejected_change_ids=["chg_2"],
        )
        assert decision.decision == "approve_partial"
        assert decision.approved_change_ids == ["chg_1"]
        assert decision.rejected_change_ids == ["chg_2"]


# ---------------------------------------------------------------------------
# T6: Approval protocol validation
# ---------------------------------------------------------------------------


class TestApprovalProtocol:
    def test_approve_all_maps_to_all_non_blocked_ids(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_1",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h1",
                repository_path="src/a.py",
                variant_ids=["var_a"],
                rationale="change 1",
            ),
            PlannedRepositoryChange(
                change_id="chg_2",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h2",
                repository_path="src/b.py",
                variant_ids=["var_b"],
                rationale="change 2",
            ),
        ]
        plan = _make_plan(changes=changes)
        request = _make_approval_request()
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1", "chg_2"],
            approved_paths=["src/a.py", "src/b.py"],
        )

        errors = validate_approval_consistency(request=request, decision=decision, plan=plan)
        assert len(errors) == 0

    def test_approve_all_rejects_extra_ids(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_1",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h1",
                repository_path="src/a.py",
                variant_ids=["var_a"],
                rationale="change 1",
            ),
        ]
        plan = _make_plan(changes=changes)
        request = _make_approval_request()
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1", "chg_nonexistent"],
            approved_paths=["src/a.py"],
        )

        errors = validate_approval_consistency(request=request, decision=decision, plan=plan)
        assert len(errors) > 0
        assert any("not in the plan" in e for e in errors)

    def test_path_scope_matches_change_ids(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_1",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h1",
                repository_path="src/a.py",
                variant_ids=["var_a"],
                rationale="change 1",
            ),
        ]
        plan = _make_plan(changes=changes)
        request = _make_approval_request()
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/other.py"],
        )

        errors = validate_approval_consistency(request=request, decision=decision, plan=plan)
        assert len(errors) > 0
        assert any("not derived from approved_change_ids" in e for e in errors)

    def test_sha_mismatch_detected(self):
        plan = _make_plan()
        request = _make_approval_request(plan_sha256="c" * 64)
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/a.py"],
            plan_sha256="d" * 64,
        )

        errors = validate_approval_consistency(request=request, decision=decision, plan=plan)
        assert len(errors) > 0
        assert any("different patch_plan_sha256" in e for e in errors)

    def test_approve_partial_with_overlapping_sets(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_1",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h1",
                repository_path="src/a.py",
                variant_ids=["var_a"],
                rationale="change 1",
            ),
        ]
        plan = _make_plan(changes=changes)
        request = _make_approval_request()
        decision = ApprovalDecision(
            decision_id="ad_test",
            decision="approve_partial",
            approved_patch_plan_sha256="c" * 64,
            approved_change_ids=["chg_1"],
            rejected_change_ids=["chg_1"],
            approved_paths=["src/a.py"],
            user_evidence_id="ev_user",
            decided_at=_NOW,
        )

        errors = validate_approval_consistency(request=request, decision=decision, plan=plan)
        assert len(errors) > 0
        assert any("both approved and rejected" in e for e in errors)

    def test_policy_deny_overrides_approval(self):
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/protected.py"],
        )

        errors = validate_approved_paths_against_policy(
            decision=decision,
            policy_denied_paths={"src/protected.py"},
        )
        assert len(errors) > 0
        assert any("policy-denied" in e for e in errors)

    def test_policy_deny_on_parent_dir(self):
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["eval/subdir/new_test.py"],
        )

        errors = validate_approved_paths_against_policy(
            decision=decision,
            policy_denied_paths={"eval"},
        )
        assert len(errors) > 0
        assert any("Ancestor" in e for e in errors)

    def test_effective_write_paths_layered_rules(self):
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1", "chg_2", "chg_3"],
            approved_paths=["src/a.py", "src/b.py", "src/c.py"],
        )

        result = compute_approval_effective_write_paths(
            decision=decision,
            planned_paths={"src/a.py", "src/b.py", "src/c.py", "src/d.py"},
            policy_denied_paths={"src/b.py"},
            policy_ask_paths={"src/c.py"},
        )

        assert result["src/b.py"] == "deny"
        assert result["src/c.py"] == "ask"
        assert result["src/a.py"] == "allow"
        assert result["src/d.py"] == "deny"


# ---------------------------------------------------------------------------
# T7: ControlledPatchApplicator
# ---------------------------------------------------------------------------


class TestControlledPatchApplicator:
    def test_can_write_path_allows_approved(self):
        app = ControlledPatchApplicator()
        assert app.can_write_path("src/a.py", {"src/a.py"}) is True

    def test_can_write_path_denies_unapproved(self):
        app = ControlledPatchApplicator()
        assert app.can_write_path("src/a.py", {"src/b.py"}) is False

    def test_can_write_path_denies_policy_denied(self):
        app = ControlledPatchApplicator(policy_denied_paths={"src/a.py"})
        assert app.can_write_path("src/a.py", {"src/a.py"}) is False

    def test_apply_patch_creates_files(self, tmp_path):
        app = ControlledPatchApplicator()

        change = PlannedRepositoryChange(
            change_id="chg_1",
            workspace_id="ws_1",
            change_kind="create",
            target_mode="new_target",
            proposed_symbol="NewModule",
            repository_path="src/new_module.py",
            variant_ids=["var_1"],
            rationale="new module for variant",
        )
        plan = _make_plan(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/new_module.py"],
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        result = app.apply_patch(
            plan=plan,
            decision=decision,
            workspace_id="ws_1",
            repository_root=repo_root,
            run_id="run_test",
        )

        assert result.overall_status == "patch_applied_and_local_validations_passed"
        assert (repo_root / "src" / "new_module.py").exists()
        content = (repo_root / "src" / "new_module.py").read_text()
        assert "new module for variant" in content

    def test_apply_patch_skips_unapproved(self, tmp_path):
        app = ControlledPatchApplicator()

        change = PlannedRepositoryChange(
            change_id="chg_1",
            workspace_id="ws_1",
            change_kind="create",
            target_mode="new_target",
            proposed_symbol="NewModule",
            repository_path="src/new_module.py",
            variant_ids=["var_1"],
            rationale="new module",
        )
        plan = _make_plan(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_other"],
            approved_paths=["src/other.py"],
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        result = app.apply_patch(
            plan=plan,
            decision=decision,
            workspace_id="ws_1",
            repository_root=repo_root,
            run_id="run_test",
        )

        assert result.overall_status == "patch_applied_and_local_validations_passed"
        assert not (repo_root / "src" / "new_module.py").exists()

    def test_rollback_removes_created_files(self, tmp_path):
        app = ControlledPatchApplicator()

        change = PlannedRepositoryChange(
            change_id="chg_1",
            workspace_id="ws_1",
            change_kind="create",
            target_mode="new_target",
            proposed_symbol="NewModule",
            repository_path="src/new_module.py",
            variant_ids=["var_1"],
            rationale="new module",
        )
        plan = _make_plan(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/new_module.py"],
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        result = app.apply_patch(
            plan=plan,
            decision=decision,
            workspace_id="ws_1",
            repository_root=repo_root,
            run_id="run_test",
        )

        assert (repo_root / "src" / "new_module.py").exists()

        rolled = app.rollback(result=result, repository_root=repo_root)
        assert rolled.overall_status == "rolled_back"
        assert not (repo_root / "src" / "new_module.py").exists()

    def test_local_validation_report(self, tmp_path):
        app = ControlledPatchApplicator()

        change = PlannedRepositoryChange(
            change_id="chg_1",
            workspace_id="ws_1",
            change_kind="create",
            target_mode="new_target",
            proposed_symbol="NewModule",
            repository_path="src/new_module.py",
            variant_ids=["var_1"],
            rationale="new module",
        )
        plan = _make_plan(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/new_module.py"],
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        result = app.apply_patch(
            plan=plan,
            decision=decision,
            workspace_id="ws_1",
            repository_root=repo_root,
            run_id="run_test",
        )

        report = app.run_local_validation(
            result=result,
            run_id="run_test",
            workspace_id="ws_1",
        )

        assert report.status == "patch_applied_and_local_validations_passed"
        assert report.syntax_check_passed is True

    def test_applicator_cannot_write_policy_denied(self, tmp_path):
        app = ControlledPatchApplicator(policy_denied_paths={"eval/metrics.py"})

        change = PlannedRepositoryChange(
            change_id="chg_1",
            workspace_id="ws_1",
            change_kind="create",
            target_mode="new_target",
            proposed_symbol="BadMetric",
            repository_path="eval/metrics.py",
            variant_ids=["var_1"],
            rationale="bad",
        )
        plan = _make_plan(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["eval/metrics.py"],
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        result = app.apply_patch(
            plan=plan,
            decision=decision,
            workspace_id="ws_1",
            repository_root=repo_root,
            run_id="run_test",
        )

        assert not (repo_root / "eval" / "metrics.py").exists()
