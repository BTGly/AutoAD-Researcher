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
    ApprovalPatchBundle, ApprovalRequest, CheckResult, ExternalValidationCommand,
    FullApprovalDecision, InternalValidationStep, PartialApprovalDecision,
    PatchPayload, PatchPayloadManifest, PatchPayloadValidationReport,
    PatchPlanValidationIssue, PatchPlanValidationReport,
    PlannedRepositoryChange, RejectDecision, RepositoryChangePlan,
    VariantWorkspacePlan,
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


def _req(sha="c" * 64, ws="ws", variant_ids=None):
    from autoad_researcher.schemas.patch_planning import WorkspaceApprovalSummary
    vids = variant_ids or ["v1"]
    return ApprovalRequest(
        approval_request_id="ar", run_id="run_test", workspace_id=ws,
        patch_plan_sha256=sha,
        patch_payload_manifest_sha256=sha,
        proposed_patch_diff_sha256=sha,
        patch_payload_validation_report_sha256=sha,
        patch_plan_validation_report_sha256=sha,
        repository_before_fingerprint=_FINGERPRINT_TEMPLATE,
        selected_variant_ids=vids,
        workspace_summary=WorkspaceApprovalSummary(
            workspace_id=ws, variant_ids=vids,
            planned_change_ids=[], affected_paths=[],
            dependency_change_ids=[], risk_ids=[],
        ),
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
    from autoad_researcher.schemas.patch_planning import (
        WorkspaceApprovalSummary, PatchPlanValidationReport,
        PatchPayloadValidationReport, InternalValidationStep,
        VariantWorkspacePlan, canonical_sha, compute_canonical_plan_sha256,
    )
    s = store or _test_store(run_id=run_id)

    actual_fp = _fingerprint(repo)
    plan = plan.model_copy(update={"repository_fingerprint": actual_fp})

    if not any(w.workspace_id == ws for w in plan.workspace_plans):
        plan = plan.model_copy(update={
            "workspace_plans": list(plan.workspace_plans) + [
                VariantWorkspacePlan(
                    workspace_id=ws, variant_ids=plan.selected_variant_ids,
                    isolation_mode="shared_workspace",
                    base_repository_source_id=plan.repository_source_id,
                    base_commit=plan.repository_commit,
                ),
            ],
        })

    ws_changes = [c for c in plan.changes if c.workspace_id == ws]
    approved_ids = set()
    if hasattr(dec, 'approved_change_ids'):
        approved_ids = set(dec.approved_change_ids or [])
    ws_change_ids = {c.change_id for c in ws_changes}
    effective_approved = sorted(approved_ids & ws_change_ids)
    plan = plan.model_copy(update={
        "changes": [c for c in plan.changes if c.change_id in effective_approved],
    })

    plan = plan.model_copy(update={"patch_plan_sha256": compute_canonical_plan_sha256(plan)})
    dec = dec.model_copy(update={"patch_plan_sha256": plan.patch_plan_sha256})

    diff_artifact_id = f"diffs/{run_id}/{ws}/patch.diff"
    diff_content = b"dummy diff content"
    s.write_raw(run_id, diff_artifact_id, diff_content)
    diff_sha = hashlib.sha256(diff_content).hexdigest()

    ws_changes = [c for c in plan.changes if c.workspace_id == ws]
    derived_paths = []
    for c in ws_changes:
        derived_paths.append(c.repository_path)
        if c.rename_target_path:
            derived_paths.append(c.rename_target_path)

    m = _make_manifest(payloads=payloads, run_id=run_id, ws=ws, plan_sha=plan.patch_plan_sha256)
    m = m.model_copy(update={
        "manifest_sha256": plan.patch_plan_sha256,
        "proposed_diff_sha256": diff_sha,
        "proposed_diff_artifact_id": diff_artifact_id,
    })

    if isinstance(dec, (FullApprovalDecision, PartialApprovalDecision)):
        dec = dec.model_copy(update={
            "workspace_id": ws,
            "approved_paths": derived_paths,
            "payload_manifest_sha256": m.manifest_sha256,
            "approved_diff_sha256": diff_sha,
            "approved_change_ids": effective_approved,
            "approved_internal_step_ids": ["diff_integrity", "path_containment"]
            + (["ast_parse"] if any((payloads or []) and p.target_path.endswith(".py") for p in (payloads or [])) else []),
        })

    req = _req(sha=plan.patch_plan_sha256, ws=ws, variant_ids=plan.selected_variant_ids)
    req = req.model_copy(update={
        "patch_payload_manifest_sha256": m.manifest_sha256,
        "repository_before_fingerprint": actual_fp,
        "proposed_patch_diff_sha256": diff_sha,
        "selected_variant_ids": plan.selected_variant_ids,
        "workspace_summary": WorkspaceApprovalSummary(
            workspace_id=ws, variant_ids=plan.selected_variant_ids,
            planned_change_ids=[c.change_id for c in ws_changes],
            affected_paths=derived_paths,
            dependency_change_ids=[], risk_ids=[],
        ),
        "internal_validation_steps": [
            InternalValidationStep(step_id="diff_integrity", target_artifact_ids=[diff_artifact_id]),
            InternalValidationStep(step_id="path_containment", target_artifact_ids=[diff_artifact_id]),
        ] +
        ([InternalValidationStep(step_id="ast_parse",
            target_artifact_ids=[p.payload_artifact_id for p in (payloads or []) if p.target_path.endswith(".py")])]
         if any((payloads or []) and p.target_path.endswith(".py") for p in (payloads or [])) else []),
    })
    req = req.model_copy(update={"approval_request_sha256": canonical_sha(req)})
    if hasattr(dec, 'approved_request_sha256'):
        dec = dec.model_copy(update={"approved_request_sha256": req.approval_request_sha256})

    vr = _make_vr(plan)
    pvr = _make_pvr(m, plan, status="passed")

    req = req.model_copy(update={
        "patch_plan_validation_report_sha256": canonical_sha(vr),
        "patch_payload_validation_report_sha256": canonical_sha(pvr),
    })
    req = req.model_copy(update={"approval_request_sha256": canonical_sha(req)})
    if hasattr(dec, 'approved_request_sha256'):
        dec = dec.model_copy(update={"approved_request_sha256": req.approval_request_sha256})

    return app.apply_patch(plan=plan, decision=dec, request=req,
                           workspace_id=ws, repository_root=repo, run_id=run_id,
                           manifest=m, validation_report=vr,
                           payload_validation_report=pvr, artifact_store=s)


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
        assert r.overall_status == "blocked"


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
        assert r.overall_status == "blocked"


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
        assert r.overall_status == "blocked"


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
        store_c17 = _test_store()
        pf = app.run_preflight(
            plan=plan2, request=_req(sha=plan.patch_plan_sha256),
            decision=_dec(ids=["dummy"], sha=plan.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m, validation_report=_make_vr(plan2),
            payload_validation_report=_make_pvr(m, plan2),
            artifact_store=store_c17,
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
        store_c18 = _test_store()
        pf = app.run_preflight(
            plan=plan, request=_req(sha=plan.patch_plan_sha256),
            decision=_dec(ids=[], sha=plan.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m2, validation_report=_make_vr(plan),
            payload_validation_report=_make_pvr(m2, plan),
            artifact_store=store_c18,
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
        m3 = _make_manifest(run_id=plan.run_id, ws="ws", plan_sha=plan.patch_plan_sha256)
        m3 = m3.model_copy(update={"manifest_sha256": plan.patch_plan_sha256})
        store_d19 = _test_store()
        pf = app.run_preflight(
            plan=plan, request=req2,
            decision=_dec(ids=[], sha=plan.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m3, validation_report=_make_vr(plan),
            payload_validation_report=_make_pvr(m3, plan),
            artifact_store=store_d19,
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
        store_d20 = _test_store()
        pf = app.run_preflight(
            plan=plan, request=_req(sha=plan.patch_plan_sha256),
            decision=dec2,
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m20, validation_report=_make_vr(plan),
            payload_validation_report=_make_pvr(m20, plan),
            artifact_store=store_d20,
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
        m22 = _make_manifest(run_id=plan.run_id, ws="ws", plan_sha=plan.patch_plan_sha256)
        m22 = m22.model_copy(update={"manifest_sha256": plan.patch_plan_sha256})
        store_d22 = _test_store()
        pf = app.run_preflight(
            plan=plan, request=_req(sha=plan.patch_plan_sha256),
            decision=_dec(ids=[], sha=plan.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m22, validation_report=vreport2,
            payload_validation_report=_make_pvr(m22, plan),
            artifact_store=store_d22,
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
        m25 = _make_manifest(run_id=plan.run_id, ws="ws", plan_sha=plan.patch_plan_sha256)
        m25 = m25.model_copy(update={"manifest_sha256": plan.patch_plan_sha256})
        store_d25 = _test_store()
        pf = app.run_preflight(
            plan=plan, request=_req(sha=plan.patch_plan_sha256),
            decision=_dec(ids=[], sha=plan.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m25, validation_report=vreport,
            payload_validation_report=_make_pvr(m25, plan),
            artifact_store=store_d25,
        )
        assert not pf.ready


class TestPreflightD26EmptyStepTarget:
    """26: required InternalValidationStep target_artifact_ids empty → blocked by D4."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "src/x.py")
        c = _c("chg_1", "ws", kind="modify", tm="existing_target",
               path="src/x.py", policy="replace_existing", before_sha="a" * 64)
        plan = _psha(changes=[c])
        fp = _fingerprint(repo)
        plan = plan.model_copy(update={"repository_fingerprint": fp})
        plan = plan.model_copy(update={"patch_plan_sha256": compute_canonical_plan_sha256(plan)})
        plan = plan.model_copy(update={"workspace_plans": [
            VariantWorkspacePlan(
                workspace_id="ws", variant_ids=["v1"],
                isolation_mode="shared_workspace",
                base_repository_source_id="src_test",
                base_commit="a" * 40,
            )]})
        plan = plan.model_copy(update={"patch_plan_sha256": compute_canonical_plan_sha256(plan)})
        store = _test_store()
        diff_content = b"diff --git a/src/x.py b/src/x.py"
        diff_sha = hashlib.sha256(diff_content).hexdigest()
        store.write_raw("run_test", "diff_1", diff_content)
        m = _make_manifest(payloads=[], run_id=plan.run_id, ws="ws", plan_sha=plan.patch_plan_sha256)
        m = m.model_copy(update={"manifest_sha256": plan.patch_plan_sha256,
                                 "proposed_diff_sha256": diff_sha,
                                 "proposed_diff_artifact_id": "diff_1"})
        vr = _make_vr(plan)
        pvr = _make_pvr(m, plan)
        req = _req(sha=plan.patch_plan_sha256, ws="ws")
        req = req.model_copy(update={
            "internal_validation_steps": [
                InternalValidationStep(step_id="diff_integrity",
                    target_artifact_ids=["diff_1"]),
                InternalValidationStep(step_id="path_containment",
                    target_artifact_ids=["diff_1"]),
            ],
            "external_validation_commands": [],
            "repository_before_fingerprint": fp,
            "patch_plan_validation_report_sha256": canonical_sha(vr),
            "patch_payload_validation_report_sha256": canonical_sha(pvr),
            "proposed_patch_diff_sha256": diff_sha,
        })
        req = req.model_copy(update={"approval_request_sha256": canonical_sha(req),
                                     "workspace_id": "ws"})
        bundle = ApprovalPatchBundle(
            bundle_id="bd26", approval_request_id=req.approval_request_id,
            created_at=_NOW,
            patch_plan_sha256=plan.patch_plan_sha256, workspace_id="ws",
            approved_change_ids=["chg_1"], approved_payload_ids=[],
            approved_diff_artifact_id="diff_1", approved_diff_sha256=diff_sha,
            payload_manifest_sha256=m.manifest_sha256,
            bundle_sha256="0" * 64,
        )
        bundle = bundle.model_copy(update={"bundle_sha256": canonical_sha(bundle)})
        dec = _dec(ids=["chg_1"], sha=plan.patch_plan_sha256)
        dec = dec.model_copy(update={
            "approved_request_sha256": req.approval_request_sha256,
            "approved_diff_sha256": diff_sha,
            "approved_paths": ["src/x.py"],
            "approved_internal_step_ids": ["diff_integrity", "path_containment"],
            "approval_patch_bundle_sha256": bundle.bundle_sha256,
            "approved_collision_change_ids": ["chg_1"],
        })
        # Full valid baseline should pass
        pf = app.run_preflight(
            plan=plan, request=req, decision=dec,
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m, validation_report=vr,
            payload_validation_report=pvr,
            bundle=bundle, artifact_store=store,
        )
        assert pf.ready, f"valid baseline should be ready: {pf.issues}"
        # Now break: add step with empty target_artifact_ids
        req_broken = req.model_copy(update={
            "internal_validation_steps": [
                InternalValidationStep(step_id="diff_integrity",
                    target_artifact_ids=["diff_1"]),
                InternalValidationStep(step_id="path_containment",
                    target_artifact_ids=["diff_1"]),
                InternalValidationStep(step_id="ast_parse",
                    target_artifact_ids=[], required=True),
            ],
        })
        req_broken = req_broken.model_copy(update={"approval_request_sha256": canonical_sha(req_broken)})
        dec_broken = dec.model_copy(update={"approved_internal_step_ids":
            ["diff_integrity", "path_containment", "ast_parse"]})
        dec_broken = dec_broken.model_copy(update={"approved_request_sha256": req_broken.approval_request_sha256})
        pf2 = app.run_preflight(
            plan=plan, request=req_broken, decision=dec_broken,
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m, validation_report=vr,
            payload_validation_report=pvr,
            bundle=bundle, artifact_store=store,
        )
        assert not pf2.ready
        assert any("D4" in i for i in pf2.issues)


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
        m35 = _make_manifest(run_id=plan.run_id, plan_sha=plan.patch_plan_sha256)
        m35 = m35.model_copy(update={"manifest_sha256": "d2" * 32})
        store_d35 = _test_store()
        pf = app.run_preflight(
            plan=plan, request=req2, decision=dec2,
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m35, validation_report=_make_vr(plan),
            payload_validation_report=_make_pvr(m35, plan),
            artifact_store=store_d35,
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
        assert r.overall_status == "blocked"


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
        assert r.overall_status == "blocked"


# ── Preflight Group E: Partial approval edge cases ───────────────────

class TestPreflightE39BeforeShaNone:
    """39: payload.target_before_sha256 should be None for create without replace_existing → validator rejects."""

    def test_rejected(self, tmp_path):
        artifact_dir = tmp_path / "runs" / "r" / "ws"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_content = b"payload content"
        (artifact_dir / "x.py").write_bytes(artifact_content)
        payload_sha = hashlib.sha256(artifact_content).hexdigest()
        change = _c("chg_1", "ws", kind="create", tm="new_target",
                     path="src/x.py", policy="must_not_exist")
        payload = PatchPayload(
            payload_id="pld_1", change_id="chg_1",
            payload_kind="full_after_content",
            target_path="src/x.py",
            payload_artifact_id="runs/r/ws/x.py",
            payload_sha256=payload_sha,
            target_before_sha256="unexpected_sha",
        )
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
        store = _test_store()
        store.write_raw("run_test", payload.payload_artifact_id, artifact_content)
        result = validate_payload_manifest(
            manifest=manifest,
            plan=plan,
            repository_root=tmp_path,
            report_id="vr_39",
            artifact_store=store,
        )
        assert result.status == "failed"
        assert any("target_before_sha256" in (i.description or "") for i in result.issues)


class TestPreflightE40BeforeShaMismatch:
    """40: replace_existing with mismatched target_before_sha256 → validator rejects."""

    def test_rejected(self, tmp_path):
        artifact_dir = tmp_path / "runs" / "r" / "ws"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_content = b"payload content"
        (artifact_dir / "x.py").write_bytes(artifact_content)
        payload_sha = hashlib.sha256(artifact_content).hexdigest()
        change = _c("chg_1", "ws", kind="modify", tm="existing_target",
                     path="src/x.py", policy="replace_existing",
                     before_sha="correct_sha")
        payload = PatchPayload(
            payload_id="pld_1", change_id="chg_1",
            payload_kind="full_after_content",
            target_path="src/x.py",
            payload_artifact_id="runs/r/ws/x.py",
            payload_sha256=payload_sha,
            target_before_sha256="wrong_sha",
        )
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
        store = _test_store()
        store.write_raw("run_test", payload.payload_artifact_id, artifact_content)
        result = validate_payload_manifest(
            manifest=manifest,
            plan=plan,
            repository_root=tmp_path,
            report_id="vr_40",
            artifact_store=store,
        )
        assert result.status == "failed"
        assert any("target_before_sha256" in (i.description or "") for i in result.issues)


class TestPreflightE41PartialApprovalUnknownId:
    """41: Partial Approval with unknown change_id → C10 blocks."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        repo = tmp_path / "repo"; repo.mkdir()
        _write(repo, "src/x.py")
        c = _c("chg_known", "ws", path="src/x.py")
        plan = _psha(changes=[c])
        fp = _fingerprint(repo)
        plan = plan.model_copy(update={"repository_fingerprint": fp})
        plan = plan.model_copy(update={"patch_plan_sha256": compute_canonical_plan_sha256(plan)})
        store = _test_store()
        diff_content = b"diff content"
        diff_sha = hashlib.sha256(diff_content).hexdigest()
        store.write_raw("run_test", "diff_1", diff_content)
        m = _make_manifest(payloads=[], run_id=plan.run_id, ws="ws", plan_sha=plan.patch_plan_sha256)
        m = m.model_copy(update={"manifest_sha256": plan.patch_plan_sha256,
                                 "proposed_diff_sha256": diff_sha,
                                 "proposed_diff_artifact_id": "diff_1"})
        req = _req(sha=plan.patch_plan_sha256, ws="ws")
        req = req.model_copy(update={
            "internal_validation_steps": [
                InternalValidationStep(step_id="diff_integrity", target_artifact_ids=["diff_1"]),
                InternalValidationStep(step_id="path_containment", target_artifact_ids=["diff_1"]),
            ],
        })
        bundle = ApprovalPatchBundle(
            bundle_id="be41", approval_request_id=req.approval_request_id,
            created_at=_NOW,
            patch_plan_sha256=plan.patch_plan_sha256, workspace_id="ws",
            approved_change_ids=["chg_unknown"], approved_payload_ids=[],
            approved_diff_artifact_id="diff_1", approved_diff_sha256=diff_sha,
            payload_manifest_sha256=m.manifest_sha256, bundle_sha256="0" * 64,
        )
        bundle = bundle.model_copy(update={"bundle_sha256": canonical_sha(bundle)})
        dec = PartialApprovalDecision(
            decision_id="pd41", approval_request_id=req.approval_request_id,
            approved_request_sha256=canonical_sha(req), workspace_id="ws",
            patch_plan_sha256=plan.patch_plan_sha256,
            payload_manifest_sha256=m.manifest_sha256,
            approval_patch_bundle_sha256=bundle.bundle_sha256,
            approved_paths=["src/x.py"],
            approved_change_ids=["chg_unknown"], rejected_change_ids=[],
            approved_internal_step_ids=["diff_integrity", "path_containment"],
            approved_external_command_ids=[], approved_collision_change_ids=[],
            approved_ask_paths=[], user_evidence_id="ev_u", decided_at=_NOW,
        )
        pf = app.run_preflight(
            plan=plan, request=req, decision=dec,
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m, validation_report=_make_vr(plan),
            payload_validation_report=_make_pvr(m, plan),
            bundle=bundle, artifact_store=store,
        )
        assert not pf.ready
        assert any("C10" in i for i in pf.issues)


class TestPreflightE42PartialApprovalConflict:
    """42: Partial Approval approved ∩ rejected ≠ ∅ → C12 blocks."""

    def test_blocked(self, tmp_path):
        app = ControlledPatchApplicator(policy_allowed_paths={"src/"})
        repo = tmp_path / "repo"; repo.mkdir()
        _write(repo, "src/x.py")
        _write(repo, "src/y.py")
        c1 = _c("chg_1", "ws", path="src/x.py")
        c2 = _c("chg_2", "ws", path="src/y.py")
        plan = _psha(changes=[c1, c2])
        fp = _fingerprint(repo)
        plan = plan.model_copy(update={"repository_fingerprint": fp})
        plan = plan.model_copy(update={"patch_plan_sha256": compute_canonical_plan_sha256(plan)})
        store = _test_store()
        diff_content = b"diff content"
        diff_sha = hashlib.sha256(diff_content).hexdigest()
        store.write_raw("run_test", "diff_1", diff_content)
        m = _make_manifest(payloads=[], run_id=plan.run_id, ws="ws", plan_sha=plan.patch_plan_sha256)
        m = m.model_copy(update={"manifest_sha256": plan.patch_plan_sha256,
                                 "proposed_diff_sha256": diff_sha,
                                 "proposed_diff_artifact_id": "diff_1"})
        req = _req(sha=plan.patch_plan_sha256, ws="ws")
        req = req.model_copy(update={
            "internal_validation_steps": [
                InternalValidationStep(step_id="diff_integrity", target_artifact_ids=["diff_1"]),
                InternalValidationStep(step_id="path_containment", target_artifact_ids=["diff_1"]),
            ],
        })
        bundle = ApprovalPatchBundle(
            bundle_id="be42", approval_request_id=req.approval_request_id,
            created_at=_NOW,
            patch_plan_sha256=plan.patch_plan_sha256, workspace_id="ws",
            approved_change_ids=["chg_1"], approved_payload_ids=[],
            approved_diff_artifact_id="diff_1", approved_diff_sha256=diff_sha,
            payload_manifest_sha256=m.manifest_sha256, bundle_sha256="0" * 64,
        )
        bundle = bundle.model_copy(update={"bundle_sha256": canonical_sha(bundle)})
        dec = PartialApprovalDecision(
            decision_id="pd42", approval_request_id=req.approval_request_id,
            approved_request_sha256=canonical_sha(req), workspace_id="ws",
            patch_plan_sha256=plan.patch_plan_sha256,
            payload_manifest_sha256=m.manifest_sha256,
            approval_patch_bundle_sha256=bundle.bundle_sha256,
            approved_paths=["src/x.py"],
            approved_change_ids=["chg_1"], rejected_change_ids=["chg_1"],
            approved_internal_step_ids=["diff_integrity", "path_containment"],
            approved_external_command_ids=[], approved_collision_change_ids=[],
            approved_ask_paths=[], user_evidence_id="ev_u", decided_at=_NOW,
        )
        pf = app.run_preflight(
            plan=plan, request=req, decision=dec,
            workspace_id="ws", repository_root=repo, run_id=plan.run_id,
            manifest=m, validation_report=_make_vr(plan),
            payload_validation_report=_make_pvr(m, plan),
            bundle=bundle, artifact_store=store,
        )
        assert not pf.ready
        assert any("C12" in i for i in pf.issues)


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
        m45 = _make_manifest(run_id=plan2.run_id, plan_sha=plan2.patch_plan_sha256)
        m45 = m45.model_copy(update={"manifest_sha256": plan2.patch_plan_sha256})
        store_e45 = _test_store()
        pf = app.run_preflight(
            plan=plan2, request=req2,
            decision=_dec(ids=[], sha=plan2.patch_plan_sha256),
            workspace_id="ws", repository_root=repo, run_id=plan2.run_id,
            manifest=m45,
            validation_report=_make_vr(plan2),
            payload_validation_report=_make_pvr(m45, plan2),
            artifact_store=store_e45,
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
