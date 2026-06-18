"""Patch planning, approval, and controlled application behavior tests.

Covers:
  - PlannedRepositoryChange model validators (change_kind/target_mode, path validation)
  - RepositoryChangePlan + canonical SHA
  - PatchConflictAnalyzer (path + symbol level)
  - PatchPlanValidator (protected paths, hooks, symbols, modifiable scope)
  - Approval protocol (consistency always checked, plan SHA binding, policy deny)
  - ControlledPatchApplicator (layered write gate, root containment, before-blob rollback, validation)
  - Schema consistency validators
  - Security tests (path traversal, ancestor deny, system path rejection)
"""

import os
from datetime import datetime, timezone

import pytest

from autoad_researcher.schemas.baseline_architecture import ModificationHook
from autoad_researcher.schemas.patch_planning import (
    ApprovalDecision,
    ApprovalRequest,
    PlannedRepositoryChange,
    RepositoryChangePlan,
    ValidationCommand,
    WorkspaceApprovalSummary,
    compute_canonical_plan_sha256,
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

_NOW = datetime.now(timezone.utc)


def _make_plan(
    *,
    run_id: str = "run_test",
    changes: list | None = None,
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


def _make_plan_with_sha(changes=None, deps=None):
    plan = _make_plan(changes=changes, deps=deps)
    plan = plan.model_copy(update={"plan_sha256": compute_canonical_plan_sha256(plan)})
    return plan


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

    def test_rename_must_have_target_path(self):
        with pytest.raises(ValueError, match="rename requires rename_target_path"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="rename",
                target_mode="existing_target",
                hook_id="hook_1",
                repository_path="src/old.py",
                variant_ids=["var_1"],
                rationale="test",
            )

    def test_existing_target_requires_hook_or_symbol_id(self):
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

    def test_valid_rename_with_target_path(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="rename",
            target_mode="existing_target",
            hook_id="hook_1",
            repository_path="src/old.py",
            rename_target_path="src/new.py",
            variant_ids=["var_1"],
            rationale="rename module",
        )
        assert change.rename_target_path == "src/new.py"

    def test_rejects_absolute_path(self):
        with pytest.raises(ValueError, match="absolute path forbidden"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="create",
                target_mode="new_target",
                proposed_symbol="Bad",
                repository_path="/etc/my_config",
                variant_ids=["var_1"],
                rationale="bad",
            )

    def test_rejects_traversal_path(self):
        with pytest.raises(ValueError, match="parent traversal forbidden"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="create",
                target_mode="new_target",
                proposed_symbol="Bad",
                repository_path="../outside.py",
                variant_ids=["var_1"],
                rationale="bad",
            )

    def test_rejects_traversal_rename_target(self):
        with pytest.raises(ValueError, match="invalid rename_target_path"):
            PlannedRepositoryChange(
                change_id="chg_001",
                workspace_id="ws_1",
                change_kind="rename",
                target_mode="existing_target",
                hook_id="hook_1",
                repository_path="src/old.py",
                rename_target_path="../../etc.py",
                variant_ids=["var_1"],
                rationale="bad",
            )


# ---------------------------------------------------------------------------
# T2: Canonical SHA
# ---------------------------------------------------------------------------


class TestCanonicalSHA:
    def test_sha_changes_with_content(self):
        plan1 = _make_plan_with_sha(
            changes=[
                PlannedRepositoryChange(
                    change_id="chg_a",
                    workspace_id="ws",
                    change_kind="modify",
                    target_mode="existing_target",
                    hook_id="h1",
                    repository_path="src/a.py",
                    variant_ids=["var_a"],
                    rationale="A",
                ),
            ]
        )
        plan2 = _make_plan_with_sha(
            changes=[
                PlannedRepositoryChange(
                    change_id="chg_a",
                    workspace_id="ws",
                    change_kind="modify",
                    target_mode="existing_target",
                    hook_id="h1",
                    repository_path="src/b.py",
                    variant_ids=["var_a"],
                    rationale="A",
                ),
            ]
        )
        assert plan1.plan_sha256 != plan2.plan_sha256

    def test_sha_includes_repo_context(self):
        plan1 = _make_plan_with_sha(
            changes=[
                PlannedRepositoryChange(
                    change_id="chg_a",
                    workspace_id="ws",
                    change_kind="modify",
                    target_mode="existing_target",
                    hook_id="h1",
                    repository_path="src/a.py",
                    variant_ids=["var_a"],
                    rationale="A",
                ),
            ]
        )
        plan1 = plan1.model_copy(update={"run_id": plan1.run_id})
        plan2 = plan1.model_copy(update={"repository_commit": "d" * 40})
        plan2 = plan2.model_copy(update={"plan_sha256": compute_canonical_plan_sha256(plan2)})
        assert plan1.plan_sha256 != plan2.plan_sha256


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

    def test_same_path_different_symbols_parameterizable(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_a",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h1",
                repository_path="src/model.py",
                variant_ids=["var_a"],
                rationale="A",
            ),
            PlannedRepositoryChange(
                change_id="chg_b",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h2",
                repository_path="src/model.py",
                variant_ids=["var_b"],
                rationale="B",
            ),
        ]
        known = {
            "h1": ModificationHook(
                hook_id="h1", hook_name="a_fn", module_path="src/model.py",
                symbol="forward", semantic_role="entrypoint",
                path_classification="modifiable_candidate",
                allowed_for_transfer_design=True,
            ),
            "h2": ModificationHook(
                hook_id="h2", hook_name="b_fn", module_path="src/model.py",
                symbol="build_memory", semantic_role="build",
                path_classification="modifiable_candidate",
                allowed_for_transfer_design=True,
            ),
        }
        result = analyze_variant_conflicts(
            changes=changes,
            variant_ids=["var_a", "var_b"],
            repository_source_id="src_test",
            repository_commit="a" * 40,
            run_id="run_test",
            analysis_id="analysis_1",
            known_hooks=known,
        )
        assert result.overall_status == "parameterizable_conflicts"

    def test_same_symbol_mutually_exclusive(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_a",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h1",
                repository_path="src/model.py",
                variant_ids=["var_a"],
                rationale="A",
            ),
            PlannedRepositoryChange(
                change_id="chg_b",
                workspace_id="ws_1",
                change_kind="modify",
                target_mode="existing_target",
                hook_id="h1",
                repository_path="src/model.py",
                variant_ids=["var_b"],
                rationale="B",
            ),
        ]
        known = {
            "h1": ModificationHook(
                hook_id="h1", hook_name="shared", module_path="src/model.py",
                symbol="forward", semantic_role="entrypoint",
                path_classification="modifiable_candidate",
                allowed_for_transfer_design=True,
            ),
        }
        result = analyze_variant_conflicts(
            changes=changes,
            variant_ids=["var_a", "var_b"],
            repository_source_id="src_test",
            repository_commit="a" * 40,
            run_id="run_test",
            analysis_id="analysis_1",
            known_hooks=known,
        )
        assert result.overall_status == "worktree_split_required"


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
        plan = _make_plan_with_sha(changes=[change])
        known_hooks = {
            "hook_1": ModificationHook(
                hook_id="hook_1", hook_name="model_forward",
                module_path="src/model.py", symbol="forward",
                semantic_role="entrypoint",
                path_classification="modifiable_candidate",
                allowed_for_transfer_design=True,
            )
        }
        report = validate_repository_change_plan(
            plan=plan, known_hooks=known_hooks,
            modifiable_paths={"src/model.py"}, protected_paths=set(),
            report_id="report_1",
        )
        assert report.status == "passed"

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
        plan = _make_plan_with_sha(changes=[change])
        known_hooks = {
            "hook_1": ModificationHook(
                hook_id="hook_1", hook_name="eval_hook",
                module_path="eval/metrics.py", symbol="compute_metric",
                semantic_role="evaluation",
                path_classification="modifiable_candidate",
                allowed_for_transfer_design=True,
            )
        }
        report = validate_repository_change_plan(
            plan=plan, known_hooks=known_hooks,
            modifiable_paths=set(), protected_paths={"eval/metrics.py"},
            report_id="report_1",
        )
        assert report.status == "failed"
        assert any("protected_path_violation" in i.category for i in report.issues)

    def test_flags_protected_ancestor(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="create",
            target_mode="new_target",
            proposed_symbol="NewThing",
            repository_path="eval/subdir/new_file.py",
            variant_ids=["var_1"],
            rationale="test",
        )
        plan = _make_plan_with_sha(changes=[change])
        report = validate_repository_change_plan(
            plan=plan, known_hooks={},
            modifiable_paths=set(), protected_paths={"eval"},
            report_id="report_1",
        )
        assert report.status == "failed"
        assert any("protected_path_violation" in i.category for i in report.issues)

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
        plan = _make_plan_with_sha(changes=[change])
        known_hooks = {
            "hook_1": ModificationHook(
                hook_id="hook_1", hook_name="p",
                module_path="src/model.py", symbol="fn",
                semantic_role="protected",
                path_classification="protected_candidate",
                allowed_for_transfer_design=False,
            )
        }
        report = validate_repository_change_plan(
            plan=plan, known_hooks=known_hooks,
            modifiable_paths={"src/model.py"}, protected_paths=set(),
            report_id="report_1",
        )
        assert report.status == "failed"
        assert any("path_classification_violation" in i.category for i in report.issues)

    def test_flags_hook_unknown(self):
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
        plan = _make_plan_with_sha(changes=[change])
        known_hooks = {
            "hook_1": ModificationHook(
                hook_id="hook_1", hook_name="u",
                module_path="src/model.py", symbol="fn",
                semantic_role="unknown",
                path_classification="unknown",
                allowed_for_transfer_design=False,
            )
        }
        report = validate_repository_change_plan(
            plan=plan, known_hooks=known_hooks,
            modifiable_paths={"src/model.py"}, protected_paths=set(),
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
            hook_id="nonexistent",
            repository_path="src/model.py",
            variant_ids=["var_1"],
            rationale="test",
        )
        plan = _make_plan_with_sha(changes=[change])
        report = validate_repository_change_plan(
            plan=plan, known_hooks={},
            modifiable_paths={"src/model.py"}, protected_paths=set(),
            report_id="report_1",
        )
        assert report.status == "failed"
        assert any("hook_reference_broken" in i.category for i in report.issues)

    def test_new_target_must_be_in_modifiable_scope(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="create",
            target_mode="new_target",
            proposed_symbol="NewClass",
            repository_path="unknown_dir/new_file.py",
            variant_ids=["var_1"],
            rationale="test",
        )
        plan = _make_plan_with_sha(changes=[change])
        report = validate_repository_change_plan(
            plan=plan, known_hooks={},
            modifiable_paths={"src", "tests"}, protected_paths=set(),
            report_id="report_1",
        )
        assert report.status == "failed"
        assert any("path_classification_violation" in i.category for i in report.issues)

    def test_new_target_in_modifiable_scope_passes(self):
        change = PlannedRepositoryChange(
            change_id="chg_001",
            workspace_id="ws_1",
            change_kind="create",
            target_mode="new_target",
            proposed_symbol="NewTest",
            repository_path="tests/test_new.py",
            variant_ids=["var_1"],
            rationale="test",
        )
        plan = _make_plan_with_sha(changes=[change])
        report = validate_repository_change_plan(
            plan=plan, known_hooks={},
            modifiable_paths={"src", "tests"}, protected_paths=set(),
            report_id="report_1",
        )
        assert report.status == "passed"


# ---------------------------------------------------------------------------
# T5: ApprovalDecision validators
# ---------------------------------------------------------------------------


class TestApprovalDecision:
    def test_approve_all_requires_changes(self):
        with pytest.raises(ValueError, match="requires approved_change_ids"):
            ApprovalDecision(
                decision_id="ad", decision="approve_all",
                approved_patch_plan_sha256="a" * 64,
                approved_change_ids=[], approved_paths=[],
                user_evidence_id="ev_u", decided_at=_NOW,
            )

    def test_reject_must_not_approve(self):
        with pytest.raises(ValueError, match="must not have approved_change_ids"):
            ApprovalDecision(
                decision_id="ad", decision="reject",
                approved_patch_plan_sha256="a" * 64,
                approved_change_ids=["chg_1"], approved_paths=["src/a.py"],
                user_evidence_id="ev_u", decided_at=_NOW,
            )

    def test_approve_all_valid(self):
        d = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/a.py"],
        )
        assert d.decision == "approve_all"


# ---------------------------------------------------------------------------
# T6: Approval protocol
# ---------------------------------------------------------------------------


class TestApprovalProtocol:
    def test_approve_all_binds_plan_sha(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_1", workspace_id="ws",
                change_kind="modify", target_mode="existing_target",
                hook_id="h1", repository_path="src/a.py",
                variant_ids=["var_a"], rationale="x",
            ),
        ]
        plan = _make_plan_with_sha(changes=changes)
        request = ApprovalRequest(
            approval_request_id="ar", run_id=plan.run_id,
            patch_plan_sha256=plan.plan_sha256,
            repository_before_fingerprint="b" * 64,
            selected_variant_ids=[],
            workspace_summaries=[],
            dependency_changes_summary=[],
            validation_commands=[],
            created_at=_NOW,
        )
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/a.py"],
            plan_sha256=plan.plan_sha256,
        )
        errors = validate_approval_consistency(request=request, decision=decision, plan=plan)
        assert len(errors) == 0

    def test_request_sha_must_match_plan(self):
        plan = _make_plan_with_sha()
        request = _make_approval_request(plan_sha256="d" * 64)
        decision = _make_approval_decision(
            decision="approve_all", approved_change_ids=["chg_1"],
            approved_paths=["src/a.py"], plan_sha256=plan.plan_sha256,
        )
        errors = validate_approval_consistency(request=request, decision=decision, plan=plan)
        assert any("does not match plan" in e for e in errors)

    def test_decision_sha_must_match_plan(self):
        plan = _make_plan_with_sha()
        request = _make_approval_request(plan_sha256=plan.plan_sha256)
        decision = _make_approval_decision(
            decision="approve_all", approved_change_ids=["chg_1"],
            approved_paths=["src/a.py"], plan_sha256="d" * 64,
        )
        errors = validate_approval_consistency(request=request, decision=decision, plan=plan)
        assert any("does not match plan" in e for e in errors)

    def test_empty_approved_paths_flagged(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_1", workspace_id="ws",
                change_kind="modify", target_mode="existing_target",
                hook_id="h1", repository_path="src/a.py",
                variant_ids=["var_a"], rationale="x",
            ),
        ]
        plan = _make_plan_with_sha(changes=changes)
        request = _make_approval_request(plan_sha256=plan.plan_sha256)
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=[],
            plan_sha256=plan.plan_sha256,
        )
        errors = validate_approval_consistency(request=request, decision=decision, plan=plan)
        assert len(errors) > 0
        assert any("approved_paths missing" in e for e in errors)

    def test_extra_approved_paths_flagged(self):
        changes = [
            PlannedRepositoryChange(
                change_id="chg_1", workspace_id="ws",
                change_kind="modify", target_mode="existing_target",
                hook_id="h1", repository_path="src/a.py",
                variant_ids=["var_a"], rationale="x",
            ),
        ]
        plan = _make_plan_with_sha(changes=changes)
        request = _make_approval_request(plan_sha256=plan.plan_sha256)
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/a.py", "src/extra.py"],
            plan_sha256=plan.plan_sha256,
        )
        errors = validate_approval_consistency(request=request, decision=decision, plan=plan)
        assert len(errors) > 0
        assert any("approved_paths contains" in e for e in errors)

    def test_policy_deny_always_wins(self):
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/protected.py"],
        )
        errors = validate_approved_paths_against_policy(
            decision=decision, policy_denied_paths={"src/protected.py"},
        )
        assert len(errors) > 0
        assert any("policy-denied" in e for e in errors)

    def test_ancestor_deny_wins(self):
        decision = _make_approval_decision(
            decision="approve_all", approved_change_ids=["chg_1"],
            approved_paths=["eval/subdir/new.py"],
        )
        errors = validate_approved_paths_against_policy(
            decision=decision, policy_denied_paths={"eval"},
        )
        assert len(errors) > 0
        assert any("Ancestor" in e for e in errors)

    def test_effective_write_paths_layered(self):
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1", "chg_2", "chg_3"],
            approved_paths=["src/a.py", "src/b.py", "src/c.py"],
        )
        result = compute_approval_effective_write_paths(
            decision=decision,
            planned_paths={"src/a.py", "src/b.py", "src/c.py", "src/d.py"},
            policy_denied_paths={"src/b.py"},
            policy_allowed_paths={"src/a.py", "src/c.py"},
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
    def test_apply_patch_creates_file_returns_applied(self, tmp_path):
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
        plan = _make_plan_with_sha(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/new_module.py"],
            plan_sha256=plan.plan_sha256,
        )
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        result = app.apply_patch(
            plan=plan, decision=decision,
            workspace_id="ws_1", repository_root=repo_root, run_id="run_test",
        )
        assert result.overall_status == "patch_applied"
        assert (repo_root / "src" / "new_module.py").exists()
        assert "new module for variant" in (repo_root / "src" / "new_module.py").read_text()

    def test_apply_patch_filters_by_workspace(self, tmp_path):
        app = ControlledPatchApplicator()
        change_a = PlannedRepositoryChange(
            change_id="chg_a", workspace_id="ws_a",
            change_kind="create", target_mode="new_target",
            proposed_symbol="A", repository_path="src/a.py",
            variant_ids=["var_a"], rationale="A",
        )
        change_b = PlannedRepositoryChange(
            change_id="chg_b", workspace_id="ws_b",
            change_kind="create", target_mode="new_target",
            proposed_symbol="B", repository_path="src/b.py",
            variant_ids=["var_b"], rationale="B",
        )
        plan = _make_plan_with_sha(changes=[change_a, change_b])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_a", "chg_b"],
            approved_paths=["src/a.py", "src/b.py"],
            plan_sha256=plan.plan_sha256,
        )
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        result = app.apply_patch(
            plan=plan, decision=decision,
            workspace_id="ws_a", repository_root=repo_root, run_id="run_test",
        )
        assert result.overall_status == "patch_applied"
        assert (repo_root / "src" / "a.py").exists()
        assert not (repo_root / "src" / "b.py").exists()

    def test_modify_preserves_original(self, tmp_path):
        app = ControlledPatchApplicator()
        change = PlannedRepositoryChange(
            change_id="chg_1",
            workspace_id="ws_1",
            change_kind="modify",
            target_mode="existing_target",
            hook_id="h1",
            repository_path="src/existing.py",
            variant_ids=["var_1"],
            rationale="add memory to forward",
        )
        plan = _make_plan_with_sha(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/existing.py"],
            plan_sha256=plan.plan_sha256,
        )
        repo_root = tmp_path / "repo"
        (repo_root / "src").mkdir(parents=True)
        (repo_root / "src" / "existing.py").write_text("def forward(x): return x")
        result = app.apply_patch(
            plan=plan, decision=decision,
            workspace_id="ws_1", repository_root=repo_root, run_id="run_test",
        )
        content = (repo_root / "src" / "existing.py").read_text()
        assert "def forward(x): return x" in content
        assert "add memory to forward" in content

    def test_apply_skips_unapproved_change_ids(self, tmp_path):
        app = ControlledPatchApplicator()
        change = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws_1",
            change_kind="create", target_mode="new_target",
            proposed_symbol="NewModule", repository_path="src/new.py",
            variant_ids=["var_1"], rationale="new module",
        )
        plan = _make_plan_with_sha(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_other"],
            approved_paths=["src/other.py"],
            plan_sha256=plan.plan_sha256,
        )
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        result = app.apply_patch(
            plan=plan, decision=decision,
            workspace_id="ws_1", repository_root=repo_root, run_id="run_test",
        )
        assert not (repo_root / "src" / "new.py").exists()

    def test_rollback_restores_before_blob(self, tmp_path):
        app = ControlledPatchApplicator()
        change = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws_1",
            change_kind="modify", target_mode="existing_target",
            hook_id="h1", repository_path="src/existing.py",
            variant_ids=["var_1"], rationale="add feature",
        )
        plan = _make_plan_with_sha(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/existing.py"],
            plan_sha256=plan.plan_sha256,
        )
        repo_root = tmp_path / "repo"
        (repo_root / "src").mkdir(parents=True)
        original = "def forward(x): return x + 1\n"
        (repo_root / "src" / "existing.py").write_text(original)
        result = app.apply_patch(
            plan=plan, decision=decision,
            workspace_id="ws_1", repository_root=repo_root, run_id="run_test",
        )
        with open(repo_root / "src" / "existing.py") as f:
            applied = f.read()
        assert applied != original
        assert "add feature" in applied

        rolled = app.rollback(result=result, repository_root=repo_root)
        assert rolled.overall_status == "rolled_back"
        restored = (repo_root / "src" / "existing.py").read_text()
        assert restored == original

    def test_rollback_removes_new_files(self, tmp_path):
        app = ControlledPatchApplicator()
        change = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws_1",
            change_kind="create", target_mode="new_target",
            proposed_symbol="New", repository_path="src/new.py",
            variant_ids=["var_1"], rationale="new file",
        )
        plan = _make_plan_with_sha(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/new.py"],
            plan_sha256=plan.plan_sha256,
        )
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        result = app.apply_patch(
            plan=plan, decision=decision,
            workspace_id="ws_1", repository_root=repo_root, run_id="run_test",
        )
        assert (repo_root / "src" / "new.py").exists()
        rolled = app.rollback(result=result, repository_root=repo_root)
        assert not (repo_root / "src" / "new.py").exists()

    def test_root_containment_blocks_escape(self, tmp_path):
        app = ControlledPatchApplicator()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "sub").mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, repo_root / "sub" / "link_out")
        change = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws_1",
            change_kind="create", target_mode="new_target",
            proposed_symbol="Bad",
            repository_path="sub/link_out/escaped.py",
            variant_ids=["var_1"], rationale="bad",
        )
        plan = _make_plan_with_sha(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["sub/link_out/escaped.py"],
            plan_sha256=plan.plan_sha256,
        )
        result = app.apply_patch(
            plan=plan, decision=decision,
            workspace_id="ws_1", repository_root=repo_root, run_id="run_test",
        )
        assert not (outside / "escaped.py").exists()

    def test_validation_passes_on_valid_syntax(self, tmp_path):
        app = ControlledPatchApplicator()
        change = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws_1",
            change_kind="create", target_mode="new_target",
            proposed_symbol="New", repository_path="src/new.py",
            variant_ids=["var_1"], rationale="new",
        )
        plan = _make_plan_with_sha(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_1"],
            approved_paths=["src/new.py"],
            plan_sha256=plan.plan_sha256,
        )
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        result = app.apply_patch(
            plan=plan, decision=decision,
            workspace_id="ws_1", repository_root=repo_root, run_id="run_test",
        )
        assert result.overall_status == "patch_applied"
        finalized = app.finalize_with_validation(
            result=result, run_id="run_test",
            workspace_id="ws_1", repository_root=repo_root,
        )
        assert finalized.overall_status == "patch_applied_and_local_validations_passed"

    def test_can_write_path_layered(self, tmp_path):
        app = ControlledPatchApplicator(
            policy_denied_paths={"eval"},
            policy_allowed_paths={"src"},
            policy_ask_paths={"src/experimental.py"},
        )
        change = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws",
            change_kind="modify", target_mode="existing_target",
            hook_id="h1", repository_path="src/existing.py",
            variant_ids=["var_1"], rationale="x",
        )
        allowed, reason = app.can_write_path(
            path="src/existing.py",
            approved_change_ids={"chg_1"},
            change=change,
            planned_paths={"src/existing.py"},
        )
        assert allowed
        allowed, reason = app.can_write_path(
            path="eval/metrics.py",
            approved_change_ids={"chg_2"},
            change=change,
            planned_paths={"eval/metrics.py"},
        )
        assert not allowed
        assert "policy-denied" in reason

    def test_can_write_path_ancestor_deny(self):
        app = ControlledPatchApplicator(
            policy_denied_paths={"eval"},
        )
        change = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws",
            change_kind="modify", target_mode="existing_target",
            hook_id="h1", repository_path="eval/subdir/deep.py",
            variant_ids=["var_1"], rationale="x",
        )
        allowed, reason = app.can_write_path(
            path="eval/subdir/deep.py",
            approved_change_ids={"chg_1"},
            change=change,
            planned_paths={"eval/subdir/deep.py"},
        )
        assert not allowed
        assert "ancestor eval" in reason.lower()

    def test_apply_patch_refuses_unapproved_change_id(self, tmp_path):
        app = ControlledPatchApplicator()
        change = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws_1",
            change_kind="create", target_mode="new_target",
            proposed_symbol="New", repository_path="src/new.py",
            variant_ids=["var_1"], rationale="new",
        )
        plan = _make_plan_with_sha(changes=[change])
        decision = _make_approval_decision(
            decision="approve_all",
            approved_change_ids=["chg_2"],
            approved_paths=["src/new.py"],
            plan_sha256=plan.plan_sha256,
        )
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        result = app.apply_patch(
            plan=plan, decision=decision,
            workspace_id="ws_1", repository_root=repo_root, run_id="run_test",
        )
        assert not (repo_root / "src" / "new.py").exists()
