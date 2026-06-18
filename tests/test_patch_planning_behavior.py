"""Patch planning, approval, and controlled application behavior tests.

Covers all P0 fixes: preflight, fail-closed result, rename, reverse rollback,
diff generation, CheckResult, hook path match, allowed_for_transfer_design,
symbol table missing, workspace layout rebind, validation commands.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoad_researcher.schemas.baseline_architecture import ModificationHook
from autoad_researcher.schemas.patch_planning import (
    ApprovalDecision, ApprovalRequest, CheckResult, PatchPlanValidationIssue,
    PatchPlanValidationReport, PlannedRepositoryChange, RepositoryChangePlan,
    ValidationCommand, compute_canonical_plan_sha256,
)
from autoad_researcher.code_agent.approval import (
    compute_approval_effective_write_paths,
    validate_approved_paths_against_policy,
    validate_approval_consistency,
)
from autoad_researcher.code_agent.conflict_analyzer import analyze_variant_conflicts, apply_workspace_layout
from autoad_researcher.code_agent.patch_applicator import ControlledPatchApplicator
from autoad_researcher.code_agent.planner_validator import validate_repository_change_plan

_NOW = datetime.now(timezone.utc)


def _fingerprint(root):
    from autoad_researcher.code_agent.patch_applicator import _fingerprint_directory
    return _fingerprint_directory(root)


def _plan(*, run_id="run_test", changes=None, deps=None, **kw):
    return RepositoryChangePlan(
        run_id=run_id, patch_plan_id="pp_test",
        repository_source_id="src_test", repository_commit="a" * 40,
        repository_fingerprint="b" * 64, selected_variant_ids=[],
        idea_id="idea_test", changes=changes or [],
        dependency_changes=deps or [],
        configuration_changes=kw.pop("configs", []),
        test_changes=kw.pop("tests", []),
        plan_sha256="c" * 64, **kw,
    )


def _psha(changes=None, deps=None, **kw):
    p = _plan(changes=changes, deps=deps, **kw)
    return p.model_copy(update={"plan_sha256": compute_canonical_plan_sha256(p)})


def _req(sha="c" * 64):
    return ApprovalRequest(
        approval_request_id="ar", run_id="run_test",
        patch_plan_sha256=sha, repository_before_fingerprint="b" * 64,
        selected_variant_ids=[], workspace_summaries=[],
        dependency_changes_summary=[], validation_commands=[], created_at=_NOW,
    )


def _dec(decision="approve_all", approved_change_ids=None, rejected=None, approved_paths=None, sha="c" * 64):
    return ApprovalDecision(
        decision_id="ad", decision=decision,
        approved_patch_plan_sha256=sha,
        approved_change_ids=approved_change_ids or [],
        rejected_change_ids=rejected or [],
        approved_paths=approved_paths or [],
        user_evidence_id="ev_u", decided_at=_NOW,
    )


def _c(cid, ws, kind="create", tm="new_target", path="src/x.py", ps=None):
    return PlannedRepositoryChange(
        change_id=cid, workspace_id=ws, change_kind=kind, target_mode=tm,
        proposed_symbol=ps or cid.upper(),
        repository_path=path, variant_ids=["v"], rationale="r",
    )


# --- P0-2: fail-closed ---

class TestFailClosedResult:
    def test_no_approved_changes_applied_returns_failed(self, tmp_path):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws_1")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_2"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws_1", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        assert r.overall_status == "patch_application_failed"

    def test_partial_application_returns_partial_failure(self, tmp_path):
        app = ControlledPatchApplicator(policy_denied_paths={"denied"})
        c1 = _c("chg_1", "ws", path="src/a.py")
        c2 = _c("chg_2", "ws", path="denied/b.py")
        plan = _psha(changes=[c1, c2])
        dec = _dec(approved_change_ids=["chg_1", "chg_2"], approved_paths=["src/a.py", "denied/b.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        assert r.overall_status == "patch_application_partial_failure"

    def test_manifest_tracks_attempted_applied_skipped(self, tmp_path):
        app = ControlledPatchApplicator(policy_denied_paths={"denied"})
        c1 = _c("chg_1", "ws", path="src/a.py")
        c2 = _c("chg_2", "ws", path="denied/b.py")
        plan = _psha(changes=[c1, c2])
        dec = _dec(approved_change_ids=["chg_1", "chg_2"], approved_paths=["src/a.py", "denied/b.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        m = r.manifests[0]
        assert set(m.attempted_change_ids) == {"chg_1", "chg_2"}
        assert m.applied_change_ids == ["chg_1"]

    def test_all_success_returns_applied(self, tmp_path):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        assert r.overall_status == "patch_applied"


# --- P0-3: CheckResult ---

class TestCheckResult:
    def test_passed_requires_passed_or_not_required(self):
        from autoad_researcher.schemas.patch_planning import PostPatchValidationReport
        rpt = PostPatchValidationReport(
            report_id="r", run_id="r", workspace_id="w", manifest_id="m",
            status="patch_applied_and_local_validations_passed",
            syntax_check=CheckResult(status="passed"),
            format_check=CheckResult(status="not_required"),
            static_check=CheckResult(status="passed"),
            type_check=CheckResult(status="not_required"),
            import_check=CheckResult(status="passed"),
            validated_at=_NOW,
        )
        assert rpt.status == "patch_applied_and_local_validations_passed"

    def test_passed_rejects_not_run(self):
        from autoad_researcher.schemas.patch_planning import PostPatchValidationReport
        with pytest.raises(ValueError, match="requires all checks"):
            PostPatchValidationReport(
                report_id="r", run_id="r", workspace_id="w", manifest_id="m",
                status="patch_applied_and_local_validations_passed",
                syntax_check=CheckResult(status="not_run"),
                validated_at=_NOW,
            )


# --- P0-4: preflight ---

class TestPreflight:
    def test_preflight_sha_checks(self, tmp_path):
        app = ControlledPatchApplicator()
        plan = _psha()
        repo = tmp_path / "repo"; repo.mkdir()
        fp = _fingerprint(repo)
        plan = plan.model_copy(update={"repository_fingerprint": fp})
        plan = plan.model_copy(update={"plan_sha256": compute_canonical_plan_sha256(plan)})
        req = _req(sha=plan.plan_sha256)
        req = req.model_copy(update={"repository_before_fingerprint": fp})
        dec = _dec(sha=plan.plan_sha256, approved_change_ids=["chg_1"])
        pf = app.run_preflight(plan=plan, request=req, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id)
        assert pf.plan_sha_valid and pf.decision_sha_valid and pf.request_sha_valid and pf.repository_fingerprint_match

    def test_preflight_detects_sha_mismatch(self, tmp_path):
        app = ControlledPatchApplicator()
        plan = _psha()
        repo = tmp_path / "repo"; repo.mkdir()
        fp = _fingerprint(repo)
        plan = plan.model_copy(update={"repository_fingerprint": fp})
        plan = plan.model_copy(update={"plan_sha256": compute_canonical_plan_sha256(plan)})
        req = _req(sha="d" * 64)
        dec = _dec(sha=plan.plan_sha256, approved_change_ids=["chg_1"])
        pf = app.run_preflight(plan=plan, request=req, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id)
        assert not pf.request_sha_valid

    def test_apply_blocks_on_preflight_fail(self, tmp_path):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        repo = tmp_path / "repo"; repo.mkdir()
        req = _req(sha="d" * 64)
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, request=req)
        assert r.overall_status == "blocked"


# --- P0-5: empty allow = deny ---

class TestDefaultDeny:
    def test_empty_allow_denies_write(self):
        app = ControlledPatchApplicator(policy_allowed_paths=set())
        c = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws", change_kind="modify",
            target_mode="existing_target", hook_id="h",
            repository_path="src/a.py", variant_ids=["v"], rationale="r",
        )
        ok, _ = app.can_write_path(path="src/a.py", approved_change_ids={"chg_1"}, change=c, planned_paths={"src/a.py"})
        assert not ok

    def test_effective_write_defaults_deny(self):
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/a.py"])
        r = compute_approval_effective_write_paths(
            decision=dec, planned_paths={"src/a.py"},
            policy_denied_paths=set(), policy_allowed_paths=set(),
        )
        assert r["src/a.py"] == "deny"


# --- P0-6: reverse rollback ---

class TestReverseRollback:
    def test_returns_original(self, tmp_path):
        app = ControlledPatchApplicator()
        c1 = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/f.py", variant_ids=["v"], rationale="first")
        c2 = PlannedRepositoryChange(change_id="chg_2", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/f.py", variant_ids=["v"], rationale="second")
        plan = _psha(changes=[c1, c2])
        dec = _dec(approved_change_ids=["chg_1", "chg_2"], approved_paths=["src/f.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; (repo / "src").mkdir(parents=True)
        original = "def f(): pass\n"
        (repo / "src" / "f.py").write_text(original)
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        rolled = app.rollback(result=r, repository_root=repo)
        assert rolled.overall_status == "rolled_back"
        assert (repo / "src" / "f.py").read_text() == original

    def test_rollback_fingerprint_verified(self, tmp_path):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        rolled = app.rollback(result=r, repository_root=repo)
        assert rolled.rollback_manifests[0].fingerprint_matches_before is True


# --- P0-7: rename ---

class TestRename:
    def test_rename_moves_file(self, tmp_path):
        app = ControlledPatchApplicator()
        c = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws", change_kind="rename",
            target_mode="existing_target", hook_id="h",
            repository_path="src/old.py", rename_target_path="src/new.py",
            variant_ids=["v"], rationale="rename",
        )
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/old.py", "src/new.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; (repo / "src").mkdir(parents=True)
        (repo / "src" / "old.py").write_text("original")
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        assert not (repo / "src" / "old.py").exists()
        assert (repo / "src" / "new.py").exists()

    def test_rename_missing_source_skipped(self, tmp_path):
        app = ControlledPatchApplicator()
        c = PlannedRepositoryChange(
            change_id="chg_1", workspace_id="ws", change_kind="rename",
            target_mode="existing_target", hook_id="h",
            repository_path="src/missing.py", rename_target_path="src/new.py",
            variant_ids=["v"], rationale="rename",
        )
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/missing.py", "src/new.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        assert r.overall_status == "patch_application_failed"


# --- P0-8: workspace layout rebind ---

class TestWorkspaceLayoutRebind:
    def test_rebind_changes_workspace_ids(self):
        c1 = PlannedRepositoryChange(change_id="chg_a", workspace_id="ws_default", change_kind="modify", target_mode="existing_target", hook_id="h1", repository_path="src/a.py", variant_ids=["va"], rationale="a")
        c2 = PlannedRepositoryChange(change_id="chg_b", workspace_id="ws_default", change_kind="modify", target_mode="existing_target", hook_id="h2", repository_path="src/b.py", variant_ids=["vb"], rationale="b")
        plan = _psha(changes=[c1, c2])
        analysis = analyze_variant_conflicts(
            changes=[c1, c2], variant_ids=["va", "vb"],
            repository_source_id="src", repository_commit="a" * 40,
            run_id="run", analysis_id="a1",
        )
        rebound = apply_workspace_layout(plan, analysis)
        assert all(c.workspace_id != "ws_default" for c in rebound.changes)


# --- P0-9: diff ---

class TestDiffGeneration:
    def test_diff_generated(self, tmp_path):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        assert r.manifests[0].patch_diff_sha256 is not None


# --- P1-1: canonical SHA order ---

class TestCanonicalSHAOrder:
    def test_order_matters(self):
        c1 = _c("chg_a", "ws", path="src/a.py")
        c2 = _c("chg_b", "ws", path="src/b.py")
        assert _psha(changes=[c1, c2]).plan_sha256 != _psha(changes=[c2, c1]).plan_sha256


# --- P1-2: validation report in approval ---

class TestApprovalWithValidation:
    def test_approve_all_non_blocked_passes(self):
        c1 = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/a.py", variant_ids=["v"], rationale="r")
        c2 = PlannedRepositoryChange(change_id="chg_2", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/b.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c1, c2])
        req = _req(sha=plan.plan_sha256)
        dec = _dec(decision="approve_all", approved_change_ids=["chg_1", "chg_2"], approved_paths=["src/a.py", "src/b.py"], sha=plan.plan_sha256)
        vrep = PatchPlanValidationReport(
            report_id="vr", run_id=plan.run_id, plan_sha256=plan.plan_sha256,
            status="failed", issues=[
                PatchPlanValidationIssue(issue_id="i1", category="policy_violation", description="blocked", artifact_ids=["chg_2"], resolution="blocked")
            ], validated_at=_NOW,
        )
        errors = validate_approval_consistency(request=req, decision=dec, plan=plan, validation_report=vrep)
        assert any("must include all non-blocked" in e for e in errors)

    def test_approve_all_non_blocked_ok(self):
        c1 = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/a.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c1])
        req = _req(sha=plan.plan_sha256)
        dec = _dec(decision="approve_all", approved_change_ids=["chg_1"], approved_paths=["src/a.py"], sha=plan.plan_sha256)
        vrep = PatchPlanValidationReport(
            report_id="vr", run_id=plan.run_id, plan_sha256=plan.plan_sha256,
            status="passed", issues=[], validated_at=_NOW,
        )
        errors = validate_approval_consistency(request=req, decision=dec, plan=plan, validation_report=vrep)
        assert len(errors) == 0


# --- P1-3: symbol table missing ---

class TestSymbolTableMissing:
    def test_returns_to_3_1(self):
        c = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", existing_symbol_id="sym_x", repository_path="src/a.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c])
        known = {"h": ModificationHook(hook_id="h", hook_name="hn", module_path="src/a.py", symbol="sym_x", semantic_role="entrypoint", path_classification="modifiable_candidate", allowed_for_transfer_design=True)}
        report = validate_repository_change_plan(plan=plan, known_hooks=known, known_symbols=set(), report_id="r")
        assert any("symbol_table_conflict" in i.category for i in report.issues)


# --- P1-4: hook path match ---

class TestHookPathMatch:
    def test_mismatched_rejected(self):
        c = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/different.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c])
        known = {"h": ModificationHook(hook_id="h", hook_name="hn", module_path="src/original.py", symbol="fn", semantic_role="entrypoint", path_classification="modifiable_candidate", allowed_for_transfer_design=True)}
        report = validate_repository_change_plan(plan=plan, known_hooks=known, report_id="r")
        assert any("hook_reference_broken" in i.category for i in report.issues)


# --- P1-5: allowed_for_transfer_design ---

class TestAllowedForTransfer:
    def test_not_allowed_rejected(self):
        c = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/a.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c])
        known = {"h": ModificationHook(hook_id="h", hook_name="hn", module_path="src/a.py", symbol="fn", semantic_role="protected", path_classification="modifiable_candidate", allowed_for_transfer_design=False)}
        report = validate_repository_change_plan(plan=plan, known_hooks=known, report_id="r")
        assert any("policy_violation" in i.category for i in report.issues)


# --- P1-6: validation command execution ---

class TestValidationCommands:
    def test_command_passes(self):
        import tempfile
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = Path(tempfile.mkdtemp())
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        cmds = [ValidationCommand(command_id="cmd_echo", label="echo", argv=["echo", "hello"])]
        rep = app.run_local_validation(result=r, run_id=plan.run_id, workspace_id="ws", repository_root=repo, commands=cmds, approved_command_ids=["cmd_echo"])
        assert rep.syntax_check.status in {"passed", "not_run"}

    def test_command_failure_detected(self):
        import tempfile
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = Path(tempfile.mkdtemp())
        r = app.apply_patch(plan=plan, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id, skip_preflight=True)
        cmds = [ValidationCommand(command_id="cmd_fail", label="static check", argv=["python", "-c", "exit(1)"])]
        rep = app.run_local_validation(result=r, run_id=plan.run_id, workspace_id="ws", repository_root=repo, commands=cmds, approved_command_ids=["cmd_fail"])
        assert rep.static_check.status == "failed"
        assert rep.status == "patch_applied_but_local_validation_failed"


# --- existing tests (preserved) ---

class TestApprovalDecision:
    def test_approve_all_requires_changes(self):
        with pytest.raises(ValueError):
            _dec(decision="approve_all")

    def test_reject_must_not_approve(self):
        with pytest.raises(ValueError):
            _dec(decision="reject", approved_change_ids=["x"])


class TestApprovalProtocol:
    def test_empty_approved_paths_flagged(self):
        c = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/a.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c])
        req = _req(sha=plan.plan_sha256)
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=[], sha=plan.plan_sha256)
        errors = validate_approval_consistency(request=req, decision=dec, plan=plan)
        assert any("approved_paths missing" in e for e in errors)

    def test_extra_approved_paths_flagged(self):
        c = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/a.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c])
        req = _req(sha=plan.plan_sha256)
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/a.py", "src/extra.py"], sha=plan.plan_sha256)
        errors = validate_approval_consistency(request=req, decision=dec, plan=plan)
        assert any("approved_paths contains extra" in e for e in errors)

    def test_policy_deny_wins(self):
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/protected.py"])
        errors = validate_approved_paths_against_policy(decision=dec, policy_denied_paths={"src/protected.py"})
        assert any("policy-denied" in e for e in errors)

    def test_ancestor_deny_wins(self):
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["eval/sub/n.py"])
        errors = validate_approved_paths_against_policy(decision=dec, policy_denied_paths={"eval"})
        assert any("Ancestor" in e for e in errors)
