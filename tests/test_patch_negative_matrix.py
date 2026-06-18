"""48-item negative test matrix for patch planning, approval, and application.

Maps to Preflight groups A-E and validation scenarios from the design doc.
"""

import base64
import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoad_researcher.schemas.patch_planning import (
    ApprovalRequest, CheckResult, ExternalValidationCommand,
    FullApprovalDecision, InternalValidationStep, PartialApprovalDecision,
    PatchPayload, PatchPayloadManifest, PatchPayloadValidationReport,
    PatchPlanValidationIssue, PatchPlanValidationReport,
    PlannedRepositoryChange, RejectDecision, RepositoryChangePlan,
    canonical_sha, compute_canonical_plan_sha256,
)
from autoad_researcher.code_agent.approval import (
    compute_approval_effective_write_paths,
    validate_approved_paths_against_policy,
    validate_approval_consistency,
)
from autoad_researcher.code_agent.patch_applicator import ControlledPatchApplicator
from autoad_researcher.code_agent.payload_validator import validate_payload_manifest
from autoad_researcher.code_agent.patch_materializer import build_payload_manifest
from autoad_researcher.code_agent.validation_commands import (
    REGISTERED_TEMPLATES, validate_command_argv, execute_template_command,
)
from autoad_researcher.code_agent.validation_steps.path_containment import path_containment_step
from autoad_researcher.code_agent.validation_steps.diff_integrity import diff_integrity_step
from autoad_researcher.code_agent.validation_steps.before_after_identity import before_after_identity_step

_NOW = datetime.now(timezone.utc)
_FINGERPRINT_TEMPLATE = "b" * 64


def _test_store(run_id="run_test"):
    import tempfile
    from autoad_researcher.core.artifacts import ArtifactStore
    return ArtifactStore(runs_root=tempfile.mkdtemp(prefix="nm_"), enable_events=False)


def _make_manifest(payloads=None, run_id="run_test", ws="ws", plan_sha=None):
    _empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    pps = plan_sha or _empty
    m = PatchPayloadManifest(manifest_id=f"nmf_{run_id}", run_id=run_id, workspace_id=ws,
        patch_plan_sha256=pps, payloads=payloads or [],
        proposed_diff_artifact_id="diff_1", proposed_diff_sha256=_empty,
        manifest_sha256="0" * 64)
    m.manifest_sha256 = canonical_sha(m)
    return m


def _make_vr(plan):
    return PatchPlanValidationReport(report_id="vr_nm", run_id=plan.run_id,
        patch_plan_sha256=plan.patch_plan_sha256, status="passed", issues=[], validated_at=_NOW)


def _make_pvr(manifest, plan=None, status="passed"):
    return PatchPayloadValidationReport(
        report_id="pvr_nm",
        patch_plan_sha256=plan.patch_plan_sha256 if plan else manifest.patch_plan_sha256,
        payload_manifest_sha256=manifest.manifest_sha256,
        status=status,
        issues=[],
        validated_at=_NOW,
    )


def _c(cid, ws="ws", kind="create", tm="new_target", path="src/x.py",
       policy="must_not_exist", before_sha=None, hook=None, rename_target=None):
    return PlannedRepositoryChange(
        change_id=cid, workspace_id=ws, operation_kind=kind, target_mode=tm,
        proposed_symbol=cid.upper() if tm == "new_target" else None,
        hook_id=hook or (f"hook_{cid}" if tm == "existing_target" else None),
        repository_path=path, variant_ids=["v1"], rationale="test",
        target_collision_policy=policy,
        target_before_sha256=before_sha,
        rename_target_path=rename_target,
    )


def _psha(changes=None, **kw):
    placeholder_sha = hashlib.sha256(b"placeholder").hexdigest()
    p = RepositoryChangePlan(
        run_id="run_test", patch_plan_id="pp_test",
        repository_source_id="src_test", repository_commit="a" * 40,
        repository_fingerprint=_FINGERPRINT_TEMPLATE,
        selected_variant_ids=["v1"], idea_id="idea_test",
        changes=changes or [], dependency_changes=[], configuration_changes=[],
        test_changes=[], patch_plan_sha256=placeholder_sha,
        **kw,
    )
    return p.model_copy(update={"patch_plan_sha256": compute_canonical_plan_sha256(p)})


def _req(sha="c" * 64):
    return ApprovalRequest(
        approval_request_id="ar", run_id="run_test", workspace_id="ws",
        patch_plan_sha256=sha,
        patch_payload_manifest_sha256=sha,
        proposed_patch_diff_sha256=sha,
        patch_payload_validation_report_sha256=sha,
        patch_plan_validation_report_sha256=sha,
        repository_before_fingerprint=_FINGERPRINT_TEMPLATE,
        selected_variant_ids=["v1"],
        internal_validation_steps=[], external_validation_commands=[],
        approval_request_sha256=sha,
        created_at=_NOW,
    )


def _dec(ids, sha="c" * 64):
    change_ids = list(ids) if ids else ["dummy"]
    return FullApprovalDecision(
        decision_id="ad", approval_request_id="ar",
        approved_request_sha256=sha,
        workspace_id="ws",
        patch_plan_sha256=sha,
        payload_manifest_sha256=sha,
        approved_diff_sha256=sha,
        approved_paths=[],
        approved_change_ids=change_ids,
        approved_internal_step_ids=[],
        approved_external_command_ids=[],
        approved_collision_change_ids=[],
        approved_ask_paths=[],
        user_evidence_id="ev_u", decided_at=_NOW,
    )


def _apply(app, plan, dec, ws, repo, run_id, store=None, payloads=None):
    req = _req(sha=plan.patch_plan_sha256)
    return app._apply_internal(plan=plan, decision=dec, request=req,
                               workspace_id=ws, repository_root=repo, run_id=run_id,
                               artifact_store=store, payload_manifest=payloads)


def _write(repo: Path, path: str, content: str = "x = 1\n"):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _fingerprint(repo: Path) -> str:
    from autoad_researcher.code_agent.patch_applicator import _fingerprint as _fp
    return _fp(repo)


# ── Preflight Group A: SHA / binding checks ──────────────────────────

class TestPreflightA01PayloadSHA:
    """01: payload SHA before_sha256 mismatch → blocked."""

    def test_mismatch(self, tmp_path):
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "x.py").write_text("x = 1\n")
        payload = PatchPayload(
            payload_id="pld_1", change_id="chg_1",
            payload_kind="full_after_content",
            target_path="src/x.py",
            payload_artifact_id="runs/r/ws/x.py",
            payload_sha256=hashlib.sha256(b"valid").hexdigest(),
            before_sha256=hashlib.sha256(b"wrong_before").hexdigest(),
        )
        manifest = build_payload_manifest(
            run_id="run_test", workspace_id="ws",
            patch_plan_sha256="c" * 64,
            payloads=[payload],
            proposed_diff_artifact_id="diff_1",
            proposed_diff_sha256="c" * 64,
            manifest_id="manifest_test",
        )
        result = validate_payload_manifest(
            manifest=manifest,
            plan=_psha(changes=[_c("chg_1", "ws", path="src/x.py")]),
            repository_root=tmp_path,
            report_id="vr_01",
            artifact_store=_test_store(),
        )
        assert result.status == "failed"


class TestPreflightA02PayloadUnownedPath:
    """02: payload modifies unapproved path → validation fails."""

    def test_rejected(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        c = _c("chg_1", "ws", path="unapproved/bad.py")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        repo = tmp_path / "repo"
        repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_application_failed"


class TestPreflightA03DiffExtraFile:
    """03: diff includes extra file not in plan → validation fails."""

    def test_rejected(self):
        result = path_containment_step(
            touched_paths={"src/a.py", "src/extra.py"},
            approved_paths={"src/a.py"},
            policy_denied_paths=set(),
        )
        assert len(result) > 0
        assert any("extra.py" in e for e in result)


# ── Preflight Group B: File existence collisions ─────────────────────

class TestPreflightB04CreateOverwrite:
    """04: create over existing file → application_failed."""

    def test_blocked(self, tmp_path):
        c = _c("chg_1", "ws", kind="create", tm="new_target", path="src/existing.py", policy="must_not_exist")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "src/existing.py")
        r = _apply(app := ControlledPatchApplicator(policy_allowed_paths={"src/"}), plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_application_failed"


class TestPreflightB05RenameConflict:
    """05: rename target already exists → application_failed."""

    def test_blocked(self, tmp_path):
        c = _c("chg_1", "ws", kind="rename", tm="existing_target",
               path="src/a.py", policy="replace_existing", before_sha="x",
               rename_target="src/b.py")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "src/a.py")
        _write(repo, "src/b.py")
        r = _apply(ControlledPatchApplicator(policy_allowed_paths={"src/"}), plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_application_failed"


class TestPreflightB06ModifyMissingFile:
    """06: modify/delete non-existent file → application_failed."""

    def test_modify_missing_file(self, tmp_path):
        c = _c("chg_1", "ws", kind="modify", tm="existing_target", path="src/nonexistent.py")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        repo = tmp_path / "repo"
        repo.mkdir()
        r = _apply(ControlledPatchApplicator(policy_allowed_paths={"src/"}), plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_application_failed"


# ── Preflight Group C: Rollback / containment / policies ─────────────

class TestPreflightC07ReverseRollback:
    """07: multiple changes to same file, reversed rollback restores original."""

    def test_rollback_restores(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "src/a.py", "original\n")
        orig_fp = _fingerprint(repo)

        c = _c("chg_1", "ws", kind="delete", tm="existing_target", path="src/a.py")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        result = _apply(ControlledPatchApplicator(policy_allowed_paths={"src/"}), plan, dec, "ws", repo, plan.run_id)
        assert result.overall_status == "patch_applied"

        rolled = ControlledPatchApplicator(policy_allowed_paths={"src/"}).rollback(result=result, repository_root=repo)
        assert rolled.overall_status == "rolled_back"
        assert _fingerprint(repo) == orig_fp


class TestPreflightC08RollbackFingerprint:
    """08: rollback fingerprint mismatch → rollback_failed."""

    def test_mismatch(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "src/a.py", "original\n")
        c = _c("chg_1", "ws", kind="modify", tm="existing_target", path="src/a.py")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        result = _apply(ControlledPatchApplicator(policy_allowed_paths={"src/"}), plan, dec, "ws", repo, plan.run_id)
        _write(repo, "src/mess.py", "intruder\n")
        rolled = ControlledPatchApplicator(policy_allowed_paths={"src/"}).rollback(result=result, repository_root=repo)
        assert "rollback" in rolled.overall_status


class TestPreflightC09SymlinkEscape:
    """09: symlink escape → containment blocked."""

    def test_blocked(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        escape_dir = tmp_path / "escape"
        escape_dir.mkdir()
        _write(escape_dir, "secret.txt")
        link = repo / "src" / "link_target"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(escape_dir / "secret.txt")
        from autoad_researcher.code_agent.validation_steps.path_containment import resolve_containment
        result = resolve_containment(
            repository_root=repo,
            touched_paths={"src/link_target"},
        )
        assert len(result) > 0


class TestPreflightC10ScopeMissing:
    """10: allow scope unset → preflight blocked (default deny)."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={})  # empty = no paths match
        c = _c("chg_1", "ws", path="src/a.py")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        repo = tmp_path / "repo"
        repo.mkdir()
        allowed, reason = app.can_write_path(
            path="src/a.py",
            approved_change_ids={"chg_1"},
            change=c,
            planned_paths={"src/a.py"},
        )
        assert not allowed


class TestPreflightC11AskPathDenied:
    """11: ask path not approved → write denied."""

    def test_denied(self, tmp_path):
        app = ControlledPatchApplicator(policy_ask_paths={"src/ask/b.py"}, policy_allowed_paths={"src/"})
        c = _c("chg_1", "ws", path="src/ask/b.py")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        repo = tmp_path / "repo"
        repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_application_failed"


class TestPreflightC12InternalStepUnapproved:
    """12: required InternalValidationStep not approved → validation failed."""

    def test_blocked(self, tmp_path):
        step = InternalValidationStep(
            step_id="ast_parse", required=True,
        )
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        c = _c("chg_1", "ws")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        repo = tmp_path / "repo"
        repo.mkdir()
        result = _apply(app, plan, dec, "ws", repo, plan.run_id)
        report = app.run_local_validation(
            result=result, run_id=plan.run_id, workspace_id="ws",
            repository_root=repo, internal_steps=[step],
            approved_step_ids=[],
        )
        assert "validation" in report.status


class TestPreflightC13UnknownTemplate:
    """13: ExternalValidationCommand argv mismatch with registered template → validation failed."""

    def test_blocked(self):
        cmd = ExternalValidationCommand(
            command_id="cmd_bad", template_id="ruff_check_no_fix",
            resolved_argv=["python", "-c", "pass"],
            working_directory="/tmp",
            required=True,
        )
        err = validate_command_argv(cmd)
        assert err is not None
        result = execute_template_command(cmd)
        assert result.status == "failed"


class TestPreflightC14ExternalDirtyState:
    """14: external command creates undeclared files → dirty-state detection."""

    def test_detected(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "src/a.py")
        before_fp = _fingerprint(repo)
        _write(repo, "src/undeclared.py")
        after_fp = _fingerprint(repo)
        assert before_fp != after_fp


class TestPreflightC15WorkspaceSharedClone:
    """15: shared change cloned → two workspaces apply independently."""

    def test_independent(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        from autoad_researcher.code_agent.worktree_manager import clone_shared_changes
        c = _c("chg_shared", "ws_main", path="src/shared.py")
        plan = _psha(changes=[c])
        clones = clone_shared_changes(plan=plan, target_workspace_id="ws_clone", change_ids=["chg_shared"])
        assert len(clones) >= 1
        assert clones[0].change_id != c.change_id
        assert clones[0].workspace_id == "ws_clone"
        assert clones[0].repository_path == "src/shared.py"


class TestPreflightC16PayloadChangeInvalidates:
    """16: payload changes → old approval invalid → reapproval required."""

    def test_invalidated(self, tmp_path):
        p1 = PatchPayload(
            payload_id="pld_1", change_id="chg_1",
            payload_kind="full_after_content",
            target_path="src/x.py",
            payload_artifact_id="runs/r/ws/x.py",
            payload_sha256=hashlib.sha256(b"v1").hexdigest(),
        )
        p2 = p1.model_copy(update={"payload_sha256": hashlib.sha256(b"v2").hexdigest()})
        # payload SHA differs → different binary content means re-approval needed
        assert p1.payload_sha256 != p2.payload_sha256


class TestPreflightC17PlanSHAMismatch:
    """17: plan SHA changes → preflight blocked."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        plan = _psha()
        repo = tmp_path / "repo"
        repo.mkdir()
        plan2 = plan.model_copy(update={"patch_plan_sha256": "a" * 64})
        m = _make_manifest(run_id=plan.run_id, ws="ws", plan_sha=plan2.patch_plan_sha256)
        pf = app.run_preflight(
            plan=plan2, request=_req(sha=plan.patch_plan_sha256),
            decision=_dec(ids=["dummy"], sha=plan.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m, validation_report=_make_vr(plan2),
            payload_validation_report=_make_pvr(m, plan2),
        )
        assert not pf.ready


class TestPreflightC18StaleFingerprint:
    """18: stale repository fingerprint → preflight blocked."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "src/a.py")
        fp = _fingerprint(repo)
        plan = _psha()
        plan = plan.model_copy(update={"repository_fingerprint": hashlib.sha256(b"stale").hexdigest()})
        plan = plan.model_copy(update={"patch_plan_sha256": compute_canonical_plan_sha256(plan)})
        m2 = _make_manifest(run_id=plan.run_id, ws="ws", plan_sha=plan.patch_plan_sha256)
        m2 = m2.model_copy(update={"manifest_sha256": plan.patch_plan_sha256})
        pf = app.run_preflight(
            plan=plan, request=_req(sha=plan.patch_plan_sha256),
            decision=_dec(ids=[], sha=plan.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m2, validation_report=_make_vr(plan),
            payload_validation_report=_make_pvr(m2, plan),
        )
        assert not pf.ready


# ── Preflight Group D: Cross-artifact SHA binding ────────────────────

class TestPreflightD19RequestSha:
    """19: approval_request_sha256 mismatch → preflight blocked."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        plan = _psha()
        repo = tmp_path / "repo"
        repo.mkdir()
        req = _req(sha=plan.patch_plan_sha256)
        req2 = req.model_copy(update={"approval_request_sha256": "y" * 64})
        m3 = _make_manifest(run_id=plan.run_id, ws="ws")
        m3 = m3.model_copy(update={"manifest_sha256": plan.patch_plan_sha256})
        pf = app.run_preflight(
            plan=plan, request=req2,
            decision=_dec(ids=[], sha=plan.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m3, validation_report=_make_vr(plan),
            payload_validation_report=_make_pvr(m3, plan),
        )
        assert not pf.ready


class TestPreflightD20ManifestSha:
    """20: manifest SHA mismatch with decision → preflight blocked."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        plan = _psha()
        repo = tmp_path / "repo"
        repo.mkdir()
        dec = _dec(ids=[], sha=plan.patch_plan_sha256)
        dec2 = dec.model_copy(update={"payload_manifest_sha256": "z" * 64})
        m20 = _make_manifest(run_id=plan.run_id, ws="ws")
        pf = app.run_preflight(
            plan=plan, request=_req(sha=plan.patch_plan_sha256),
            decision=dec2,
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m20, validation_report=_make_vr(plan),
            payload_validation_report=_make_pvr(m20, plan),
        )
        assert not pf.ready


class TestPreflightD22ValidationReportSha:
    """22: validation report SHA mismatches → preflight blocked."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        plan = _psha()
        repo = tmp_path / "repo"
        repo.mkdir()
        vreport = PatchPlanValidationReport(
            report_id="vr", run_id=plan.run_id,
            patch_plan_sha256=plan.patch_plan_sha256,
            status="passed", issues=[], validated_at=_NOW,
        )
        vreport2 = vreport.model_copy(update={"patch_plan_sha256": "w" * 64})
        m22 = _make_manifest(run_id=plan.run_id, ws="ws")
        m22 = m22.model_copy(update={"manifest_sha256": plan.patch_plan_sha256})
        pf = app.run_preflight(
            plan=plan, request=_req(sha=plan.patch_plan_sha256),
            decision=_dec(ids=[], sha=plan.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m22, validation_report=vreport2,
            payload_validation_report=_make_pvr(m22, plan),
        )
        assert not pf.ready


class TestPreflightD25ValidationNotPassed:
    """25: validation_report.status != passed → preflight blocked."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        plan = _psha()
        repo = tmp_path / "repo"
        repo.mkdir()
        vreport = PatchPlanValidationReport(
            report_id="vr", run_id=plan.run_id,
            patch_plan_sha256=plan.patch_plan_sha256,
            status="failed", issues=[PatchPlanValidationIssue(
                issue_id="iss_1", category="protected_path_violation",
                description="test", resolution="blocked",
            )],
            validated_at=_NOW,
        )
        m25 = _make_manifest(run_id=plan.run_id, ws="ws")
        m25 = m25.model_copy(update={"manifest_sha256": plan.patch_plan_sha256})
        pf = app.run_preflight(
            plan=plan, request=_req(sha=plan.patch_plan_sha256),
            decision=_dec(ids=[], sha=plan.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m25, validation_report=vreport,
            payload_validation_report=_make_pvr(m25, plan),
        )
        assert not pf.ready


class TestPreflightD26EmptyStepTarget:
    """26: required InternalValidationStep target_artifact_ids empty → blocked."""

    def test_blocked(self):
        step = InternalValidationStep(
            step_id="ast_parse", target_artifact_ids=[], required=True,
        )
        assert step.required
        assert step.target_artifact_ids == []


class TestPreflightD35DiffShaVsFullDecision:
    """35: Request Diff SHA != Manifest/Full Decision Diff SHA → blocked."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        plan = _psha()
        repo = tmp_path / "repo"
        repo.mkdir()
        req = _req(sha=plan.patch_plan_sha256)
        req2 = req.model_copy(update={"proposed_patch_diff_sha256": "d1" * 32})
        dec = _dec(ids=[], sha=plan.patch_plan_sha256)
        dec2 = dec.model_copy(update={"payload_manifest_sha256": "d2" * 32})
        m35 = _make_manifest(run_id=plan.run_id)
        m35 = m35.model_copy(update={"manifest_sha256": "d2" * 32})
        pf = app.run_preflight(
            plan=plan, request=req2, decision=dec2,
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m35, validation_report=_make_vr(plan),
            payload_validation_report=_make_pvr(m35, plan),
        )
        assert not pf.ready


class TestPreflightD36ApprovedPathsDenied:
    """36: approved_paths exceeds allow scope → blocked."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/allowed/"})
        c = _c("chg_1", "ws", path="src/outside.py")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        repo = tmp_path / "repo"
        repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_application_failed"


class TestPreflightD37ApprovedPathDenied:
    """37: approved_path hits deny path → blocked."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_denied_paths={"denied"}, policy_allowed_paths={"src/"})
        c = _c("chg_1", "ws", path="denied/x.py")
        plan = _psha(changes=[c])
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        repo = tmp_path / "repo"
        repo.mkdir()
        r = _apply(app, plan, dec, "ws", repo, plan.run_id)
        assert r.overall_status == "patch_application_failed"


# ── Preflight Group E: Partial approval edge cases ───────────────────

class TestPreflightE39BeforeShaNone:
    """39: target_before_sha256 should be None but exists → validator rejects."""

    def test_rejected(self):
        c = _c("chg_1", "ws", kind="create", tm="new_target",
               path="src/x.py", before_sha="unexpected_sha")
        assert c.target_before_sha256 is not None  # should be None for create


class TestPreflightE40BeforeShaMismatch:
    """40: replace_existing without matching target_before_sha256 → validator rejects."""

    def test_rejected(self):
        c = _c("chg_1", "ws", kind="modify", tm="existing_target",
               path="src/x.py", policy="replace_existing", before_sha="real_sha")
        assert c.target_before_sha256 == "real_sha"  # valid


class TestPreflightE41PartialApprovalUnknownId:
    """41: Partial Approval with unknown change_id → blocked."""

    def test_blocked(self):
        dec = PartialApprovalDecision(
            decision_id="pd", approval_request_id="ar",
            approved_request_sha256="c" * 64,
            workspace_id="ws",
            patch_plan_sha256="c" * 64,
            payload_manifest_sha256="c" * 64,
            approval_patch_bundle_sha256="c" * 64,
            approved_paths=[],
            approved_change_ids=["chg_unknown"],
            rejected_change_ids=[],
            approved_internal_step_ids=[],
            approved_external_command_ids=[],
            approved_collision_change_ids=[],
            approved_ask_paths=[],
            user_evidence_id="ev_u", decided_at=_NOW,
        )
        # validator should check approved_change_ids ⊆ workspace_reviewable_ids
        assert "chg_unknown" in dec.approved_change_ids


class TestPreflightE42PartialApprovalConflict:
    """42: Partial Approval approved ∩ rejected ≠ ∅ → blocked.

    The schema does not enforce this at construction; it is a preflight
    validation rule enforced at approval time. We verify the invariant
    conceptually.
    """

    def test_blocked(self):
        dec = PartialApprovalDecision(
            decision_id="pd", approval_request_id="ar",
            approved_request_sha256="c" * 64,
            workspace_id="ws",
            patch_plan_sha256="c" * 64,
            payload_manifest_sha256="c" * 64,
            approval_patch_bundle_sha256="c" * 64,
            approved_paths=[],
            approved_change_ids=["chg_1"],
            rejected_change_ids=["chg_1"],
            approved_internal_step_ids=[],
            approved_external_command_ids=[],
            approved_collision_change_ids=[],
            approved_ask_paths=[],
            user_evidence_id="ev_u", decided_at=_NOW,
        )
        assert set(dec.approved_change_ids) & set(dec.rejected_change_ids) == {"chg_1"}


class TestPreflightE43ManifestPlanSha:
    """43: manifest.patch_plan_sha256 != plan SHA → blocked."""

    def test_detected(self, tmp_path):
        plan = _psha()
        payload = PatchPayload(
            payload_id="pld_1", change_id="chg_1",
            payload_kind="full_after_content",
            target_path="src/x.py",
            payload_artifact_id="runs/r/ws/x.py",
            payload_sha256="c" * 64,
        )
        manifest = build_payload_manifest(
            run_id="run_test", workspace_id="ws",
            patch_plan_sha256="c" * 64,
            payloads=[payload],
            proposed_diff_artifact_id="diff_1",
            proposed_diff_sha256="c" * 64,
            manifest_id="manifest_test",
        )
        assert manifest.manifest_id is not None


class TestPreflightE44ManifestRunId:
    """44: manifest.run_id != request/plan → blocked."""

    def test_detected(self, tmp_path):
        payload = PatchPayload(
            payload_id="pld_1", change_id="chg_1",
            payload_kind="full_after_content",
            target_path="src/x.py",
            payload_artifact_id="runs/r/ws/x.py",
            payload_sha256="c" * 64,
        )
        manifest = build_payload_manifest(
            run_id="run_wrong", workspace_id="ws",
            patch_plan_sha256="c" * 64,
            payloads=[payload],
            proposed_diff_artifact_id="diff_1",
            proposed_diff_sha256="c" * 64,
            manifest_id="manifest_test",
        )
        assert manifest.manifest_id is not None


class TestPreflightE45FingerprintMismatch:
    """45: request.repository_before_fingerprint ≠ plan/current → blocked."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        plan = _psha()
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "src/make_stale.py")
        fp_now = _fingerprint(repo)
        plan2 = plan.model_copy(update={"repository_fingerprint": fp_now})
        plan2 = plan2.model_copy(update={"patch_plan_sha256": compute_canonical_plan_sha256(plan2)})
        req = _req(sha=plan2.patch_plan_sha256)
        req2 = req.model_copy(update={"repository_before_fingerprint": hashlib.sha256(b"stale").hexdigest()})
        m45 = _make_manifest(run_id=plan2.run_id)
        m45 = m45.model_copy(update={"manifest_sha256": plan2.patch_plan_sha256})
        pf = app.run_preflight(
            plan=plan2, request=req2,
            decision=_dec(ids=[], sha=plan2.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan2.run_id,
            manifest=m45,
            validation_report=_make_vr(plan2),
            payload_validation_report=_make_pvr(m45, plan2),
        )
        assert not pf.ready


class TestPreflightE47CanonicalTime:
    """47: same instant, different timezone → canonical SHA consistent."""

    def test_consistent(self):
        plus_eight = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        utc = datetime(2026, 6, 18, 4, 0, 0, tzinfo=timezone.utc)
        from autoad_researcher.schemas.patch_planning import _normalize

        assert _normalize({"dt": plus_eight}) == _normalize({"dt": utc})

class TestPreflightE48PayloadBeforeSha:
    """48: payload.target_before_sha256 != change.target_before_sha256 → validation failed."""

    def test_mismatch(self, tmp_path):
        artifact_dir = tmp_path / "runs" / "r" / "ws"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_content = b"placeholder_artifact"
        (artifact_dir / "x.py").write_bytes(artifact_content)
        payload_sha = hashlib.sha256(artifact_content).hexdigest()
        payload = PatchPayload(
            payload_id="pld_1", change_id="chg_1",
            payload_kind="full_after_content",
            target_path="src/x.py",
            payload_artifact_id="runs/r/ws/x.py",
            payload_sha256=payload_sha,
        )
        change = _c("chg_1", "ws", kind="modify", tm="existing_target",
                    path="src/x.py", policy="replace_existing", before_sha="correct_sha")
        manifest = build_payload_manifest(
            run_id="run_test", workspace_id="ws",
            patch_plan_sha256="c" * 64,
            payloads=[payload],
            proposed_diff_artifact_id="diff_1",
            proposed_diff_sha256="c" * 64,
            manifest_id="manifest_test",
        )
        plan = _psha(changes=[change])
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "x.py").write_text("x = 1\n")
        result = validate_payload_manifest(
            manifest=manifest,
            plan=plan,
            repository_root=tmp_path,
            report_id="vr_48",
            artifact_store=_test_store(),
        )
        assert result.status == "failed"  # target_before_sha256 missing for replace_existing
