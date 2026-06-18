"""Patch planning, approval, and controlled application behavior tests."""

import base64
import tempfile
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


def _dec(decision="approve_all", approved_change_ids=None, rejected=None,
         approved_paths=None, sha="c" * 64, approved_ask_paths=None):
    return ApprovalDecision(
        decision_id="ad", decision=decision,
        approved_patch_plan_sha256=sha,
        approved_change_ids=approved_change_ids or [],
        rejected_change_ids=rejected or [],
        approved_paths=approved_paths or [],
        approved_ask_paths=approved_ask_paths or [],
        user_evidence_id="ev_u", decided_at=_NOW,
    )


def _c(cid, ws, kind="create", tm="new_target", path="src/x.py", ps=None):
    return PlannedRepositoryChange(
        change_id=cid, workspace_id=ws, change_kind=kind, target_mode=tm,
        proposed_symbol=ps or cid.upper(),
        repository_path=path, variant_ids=["v"], rationale="r",
    )


def _apply(app, plan, dec, ws, repo, run_id):
    return app._apply_internal(plan=plan, decision=dec, request=_req(sha=plan.plan_sha256),
                               workspace_id=ws, repository_root=repo, run_id=run_id)


# --- P0-2: fail-closed ---

class TestFailClosedResult:
    def test_no_approved_returns_failed(self, tmp_path):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws_1")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_2"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = _apply(app, plan, dec, "ws_1", repo, plan.run_id)
        assert r.overall_status == "patch_application_failed"

    def test_partial_failure(self, tmp_path):
        app = ControlledPatchApplicator(policy_denied_paths={"denied"})
        c1 = _c("chg_1", "ws", path="src/a.py")
        c2 = _c("chg_2", "ws", path="denied/b.py")
        plan = _psha(changes=[c1, c2])
        dec = _dec(approved_change_ids=["chg_1", "chg_2"], approved_paths=["src/a.py", "denied/b.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_application_partial_failure"

    def test_manifest_tracks(self, tmp_path):
        app = ControlledPatchApplicator(policy_denied_paths={"denied"})
        c1 = _c("chg_1", "ws", path="src/a.py")
        c2 = _c("chg_2", "ws", path="denied/b.py")
        plan = _psha(changes=[c1, c2])
        dec = _dec(approved_change_ids=["chg_1", "chg_2"], approved_paths=["src/a.py", "denied/b.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        m = r.manifests[0]
        assert set(m.attempted_change_ids) == {"chg_1", "chg_2"}
        assert m.applied_change_ids == ["chg_1"]
        assert m.skipped_change_ids == ["chg_2"]
        assert r.overall_status == "patch_application_partial_failure"

    def test_all_success(self, tmp_path):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_applied"


# --- P0-4: preflight ---

class TestPreflight:
    def test_sha_checks(self, tmp_path):
        app = ControlledPatchApplicator()
        plan = _psha()
        repo = tmp_path / "repo"; repo.mkdir()
        fp = _fp(repo)
        plan = plan.model_copy(update={"repository_fingerprint": fp})
        plan = plan.model_copy(update={"plan_sha256": compute_canonical_plan_sha256(plan)})
        req = _req(sha=plan.plan_sha256)
        dec = _dec(sha=plan.plan_sha256, approved_change_ids=["chg_1"])
        pf = app.run_preflight(plan=plan, request=req, decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id)
        assert pf.plan_sha_valid and pf.decision_sha_valid and pf.request_sha_valid

    def test_sha_mismatch(self, tmp_path):
        app = ControlledPatchApplicator()
        plan = _psha(); repo = tmp_path / "repo"; repo.mkdir()
        fp = _fp(repo)
        plan = plan.model_copy(update={"repository_fingerprint": fp})
        plan = plan.model_copy(update={"plan_sha256": compute_canonical_plan_sha256(plan)})
        dec = _dec(sha=plan.plan_sha256, approved_change_ids=["chg_1"])
        pf = app.run_preflight(plan=plan, request=_req(sha="d" * 64), decision=dec, workspace_id="ws", repository_root=repo, run_id=plan.run_id)
        assert not pf.request_sha_valid

    def test_blocks_on_fail(self, tmp_path):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        repo = tmp_path / "repo"; repo.mkdir()
        req = _req(sha="d" * 64)
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        r = app.apply_patch(plan=plan, decision=dec, request=req, workspace_id="ws", repository_root=repo, run_id=plan.run_id)
        assert r.overall_status == "blocked"


# --- P0-5: base64 blob ---

class TestBase64Blob:
    def test_blob_is_base64(self, tmp_path):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        blob = r.rollback_manifests[0].rollback_blobs[0]
        base64.b64decode(blob)


# --- P0-6: modify missing fails ---

class TestModifyMissingFile:
    def test_modify_missing_fails(self, tmp_path):
        app = ControlledPatchApplicator()
        c = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/no.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/no.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_application_failed"
        assert not (repo / "src" / "no.py").exists()

    def test_delete_missing_fails(self, tmp_path):
        app = ControlledPatchApplicator()
        c = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="delete", target_mode="existing_target", hook_id="h", repository_path="src/no.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/no.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_application_failed"


# --- P0-8: check_kind validation ---

class TestCheckKind:
    def test_check_kind_routing(self):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = Path(tempfile.mkdtemp())
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        cmds = [ValidationCommand(command_id="c1", label="lint", check_kind="static", argv=["echo", "ok"])]
        rep = app.run_local_validation(result=r, run_id=plan.run_id, workspace_id="ws", repository_root=repo, commands=cmds, approved_command_ids=["c1"])
        assert rep.static_check.status == "passed"

    def test_required_not_approved(self):
        app = ControlledPatchApplicator()
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/x.py"], sha=plan.plan_sha256)
        repo = Path(tempfile.mkdtemp())
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        cmds = [ValidationCommand(command_id="c1", label="must", check_kind="format", required=True, argv=["echo"])]
        rep = app.run_local_validation(result=r, run_id=plan.run_id, workspace_id="ws", repository_root=repo, commands=cmds, approved_command_ids=[])
        assert "not approved" in str(rep.issues)


# --- P0-9: guard finalize ---

class TestFinalizeGuard:
    def test_failed_not_finalized(self):
        app = ControlledPatchApplicator()
        from autoad_researcher.schemas.patch_planning import PatchExecutionResult
        result = PatchExecutionResult(result_id="r", run_id="r", overall_status="patch_application_failed", next_stage="replan_required")
        r = app.finalize_with_validation(result=result, run_id="r", workspace_id="w", repository_root=Path(tempfile.mkdtemp()))
        assert r.next_stage != "eligible_for_runner_intake"


# --- P1-2: ask approval ---

class TestAskApproval:
    def test_ask_path_allowed_when_approved(self, tmp_path):
        app = ControlledPatchApplicator(policy_ask_paths={"src/ask.py"})
        c = _c("chg_1", "ws", path="src/ask.py")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/ask.py"], approved_ask_paths=["src/ask.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        assert (repo / "src" / "ask.py").exists()


# --- existing tests ---

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
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        rolled = app.rollback(result=r, repository_root=repo)
        assert (repo / "src" / "f.py").read_text() == original


class TestRename:
    def test_rename(self, tmp_path):
        app = ControlledPatchApplicator()
        c = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="rename", target_mode="existing_target", hook_id="h", repository_path="src/old.py", rename_target_path="src/new.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c])
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/old.py", "src/new.py"], sha=plan.plan_sha256)
        repo = tmp_path / "repo"; (repo / "src").mkdir(parents=True)
        (repo / "src" / "old.py").write_text("orig")
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        assert not (repo / "src" / "old.py").exists()
        assert (repo / "src" / "new.py").exists()


class TestApprovalDecision:
    def test_reject(self):
        with pytest.raises(ValueError):
            _dec(decision="reject", approved_change_ids=["x"])


class TestApprovalProtocol:
    def test_empty_paths_flagged(self):
        c = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/a.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c])
        req = _req(sha=plan.plan_sha256)
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=[], sha=plan.plan_sha256)
        errors = validate_approval_consistency(request=req, decision=dec, plan=plan)
        assert any("approved_paths missing" in e for e in errors)

    def test_policy_deny(self):
        dec = _dec(approved_change_ids=["chg_1"], approved_paths=["src/p.py"])
        errors = validate_approved_paths_against_policy(decision=dec, policy_denied_paths={"src/p.py"})
        assert any("policy-denied" in e for e in errors)


class TestValidationWithReport:
    def test_approve_all_non_blocked(self):
        c1 = PlannedRepositoryChange(change_id="chg_1", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/a.py", variant_ids=["v"], rationale="r")
        c2 = PlannedRepositoryChange(change_id="chg_2", workspace_id="ws", change_kind="modify", target_mode="existing_target", hook_id="h", repository_path="src/b.py", variant_ids=["v"], rationale="r")
        plan = _psha(changes=[c1, c2])
        dec = _dec(decision="approve_all", approved_change_ids=["chg_1", "chg_2"], approved_paths=["src/a.py", "src/b.py"], sha=plan.plan_sha256)
        vrep = PatchPlanValidationReport(report_id="vr", run_id=plan.run_id, plan_sha256=plan.plan_sha256, status="failed", issues=[
            PatchPlanValidationIssue(issue_id="i1", category="policy_violation", description="blocked", artifact_ids=["chg_2"], affected_change_ids=["chg_2"], resolution="blocked")
        ], validated_at=_NOW)
        errors = validate_approval_consistency(request=_req(sha=plan.plan_sha256), decision=dec, plan=plan, validation_report=vrep)
        assert any("must include all non-blocked" in e for e in errors)


def _fp(root):
    from autoad_researcher.code_agent.patch_applicator import _fingerprint
    return _fingerprint(root)
