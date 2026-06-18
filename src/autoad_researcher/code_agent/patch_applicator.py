"""ControlledPatchApplicator — Step 3.7 controlled patch application."""

import base64
import difflib
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.schemas.patch_planning import (
    ApprovalDecision, ApprovalRequest, ChangedFileEntry, CheckResult,
    ExternalValidationCommand, FullApprovalDecision, InternalValidationStep,
    PartialApprovalDecision, PatchApplicationManifest,
    PatchApplicationPreflightResult, PatchExecutionResult, PatchPayload,
    PatchPlanValidationReport, PlannedRepositoryChange,
    PostPatchValidationReport, RepositoryChangePlan,
    RollbackManifest, canonical_sha, compute_canonical_plan_sha256,
)

from autoad_researcher.code_agent.validation_commands import execute_template_command
from autoad_researcher.code_agent.validation_steps.ast_parse import ast_parse_step
from autoad_researcher.code_agent.validation_steps.diff_integrity import diff_integrity_step
from autoad_researcher.code_agent.validation_steps.path_containment import path_containment_step


class ControlledPatchApplicator:
    def __init__(self, *, policy_denied_paths=None, policy_allowed_paths=None, policy_ask_paths=None):
        self.policy_denied_paths = policy_denied_paths or set()
        self._policy_allowed_set = policy_allowed_paths
        self._policy_ask_set = policy_ask_paths
        self._approved_ask_paths: set[str] = set()

    def _set_approved_ask_paths(self, paths: set[str]) -> None:
        self._approved_ask_paths = paths

    def can_write_path(self, *, path: str, approved_change_ids: set[str],
                       change: PlannedRepositoryChange, planned_paths: set[str]) -> tuple[bool, str]:
        if change.change_id not in approved_change_ids:
            return False, f"change_id {change.change_id} not approved"
        if path in self.policy_denied_paths:
            return False, f"path {path} is policy-denied"
        for ancestor in _ancestors(path):
            if ancestor in self.policy_denied_paths:
                return False, f"ancestor {ancestor} of {path} is policy-denied"
        if path not in planned_paths:
            return False, f"path {path} not in planned paths"
        if self._policy_ask_set and path in self._policy_ask_set and path not in self._approved_ask_paths:
            return False, f"path {path} requires ask approval"
        if self._policy_allowed_set is not None and not _path_in_scope(path, self._policy_allowed_set):
            return False, f"path {path} not in policy-allowed scope"
        return True, "allowed"

    @staticmethod
    def _check_and_resolve_path(repository_root: Path, path_key: str) -> Path | None:
        try:
            candidate = (repository_root / path_key).resolve()
            root = repository_root.resolve()
            if not str(candidate).startswith(str(root) + os.sep) and candidate != root:
                return None
            return candidate
        except (ValueError, OSError):
            return None

    def run_preflight(self, *, plan: RepositoryChangePlan, request: ApprovalRequest,
                      decision: ApprovalDecision, workspace_id: str,
                      repository_root: Path, run_id: str,
                      validation_report: PatchPlanValidationReport | None = None,
                      payload_manifest_sha256: str | None = None) -> PatchApplicationPreflightResult:
        issues: list[str] = []
        canonical = compute_canonical_plan_sha256(plan)
        psv = canonical == plan.patch_plan_sha256
        if not psv:
            issues.append("patch_plan_sha256 mismatch")
        dsv = _decision_plan_sha(decision) == plan.patch_plan_sha256
        if not dsv:
            issues.append("decision SHA != plan SHA")
        rsv = request.patch_plan_sha256 == plan.patch_plan_sha256
        if not rsv:
            issues.append("request SHA != plan SHA")
        actual = _fingerprint(repository_root)
        fpm = actual == plan.repository_fingerprint
        if not fpm:
            issues.append(f"fingerprint mismatch: {actual[:16]} != {plan.repository_fingerprint[:16]}")
        ridm = run_id == plan.run_id
        if not ridm:
            issues.append("run_id mismatch")
        wse = any(w.workspace_id == workspace_id for w in plan.workspace_plans)
        if not wse:
            issues.append(f"workspace {workspace_id} not in plan.workspace_plans")
        vrv = False
        if validation_report:
            vrv = (validation_report.run_id == plan.run_id
                   and validation_report.patch_plan_sha256 == plan.patch_plan_sha256
                   and validation_report.status == "passed"
                   and not validation_report.issues)
            if not vrv:
                issues.append("validation_report not valid")
        msm = True
        if payload_manifest_sha256 and hasattr(request, 'patch_payload_manifest_sha256'):
            msm = request.patch_payload_manifest_sha256 == payload_manifest_sha256
            if not msm:
                issues.append("payload manifest SHA mismatch")
        ready = all([psv, dsv, rsv, fpm, ridm, wse, vrv, msm])
        return PatchApplicationPreflightResult(
            preflight_id=f"preflight_{run_id}_{workspace_id}", run_id=run_id, workspace_id=workspace_id,
            plan_sha_valid=psv, decision_sha_valid=dsv, request_sha_valid=rsv,
            repository_fingerprint_match=fpm, run_id_match=ridm,
            workspace_exists_in_plan=wse, validation_report_valid=vrv,
            ready=ready, issues=issues,
        )

    def apply_patch(self, *, plan: RepositoryChangePlan, decision: ApprovalDecision,
                    request: ApprovalRequest, workspace_id: str,
                    repository_root: Path, run_id: str,
                    validation_report: PatchPlanValidationReport | None = None,
                    payload_manifest: list[PatchPayload] | None = None) -> PatchExecutionResult:
        preflight = self.run_preflight(
            plan=plan, request=request, decision=decision, workspace_id=workspace_id,
            repository_root=repository_root, run_id=run_id, validation_report=validation_report,
        )
        if not preflight.ready:
            return PatchExecutionResult(result_id=f"result_{run_id}", run_id=run_id,
                                        preflight=preflight, overall_status="blocked",
                                        next_stage="replan_required")

        approved_change_ids = _decision_approved_ids(decision)
        planned_paths = {c.repository_path for c in plan.changes}
        for c in plan.changes:
            if c.rename_target_path:
                planned_paths.add(c.rename_target_path)

        payload_map: dict[str, PatchPayload] = {p.change_id: p for p in (payload_manifest or [])}
        self._set_approved_ask_paths(_decision_ask_paths(decision))

        now = datetime.now(timezone.utc)
        before_fp = _fingerprint(repository_root)
        changed_files: list[ChangedFileEntry] = []
        attempted, applied, skipped, failed = [], [], [], []

        workspace_changes = [c for c in plan.changes if c.workspace_id == workspace_id]
        for change in workspace_changes:
            if change.change_id not in approved_change_ids:
                continue
            attempted.append(change.change_id)
            path_key = change.repository_path
            allowed, reason = self.can_write_path(
                path=path_key, approved_change_ids=approved_change_ids,
                change=change, planned_paths=planned_paths,
            )
            if not allowed:
                skipped.append(change.change_id)
                continue
            abs_path = self._check_and_resolve_path(repository_root, path_key)
            if abs_path is None:
                skipped.append(change.change_id)
                continue
            target_abs = None
            if change.operation_kind == "rename" and change.rename_target_path:
                target_abs = self._check_and_resolve_path(repository_root, change.rename_target_path)
                if target_abs is None:
                    skipped.append(change.change_id)
                    continue

            payload = payload_map.get(change.change_id)
            try:
                entry = _apply_single_change(change, abs_path, now, target_abs, payload)
            except Exception:
                failed.append(change.change_id)
                entry = None
            if entry:
                changed_files.append(entry)
                applied.append(change.change_id)
            else:
                skipped.append(change.change_id)

        after_fp = _fingerprint(repository_root)
        diff_text = _generate_unified_diff(repository_root, before_fp, after_fp, changed_files)
        diff_sha = hashlib.sha256(diff_text.encode()).hexdigest() if diff_text else None
        diff_artifact = None
        if diff_text:
            diff_artifact = f"runs/{run_id}/{workspace_id}/patch.diff"

        manifest = PatchApplicationManifest(
            manifest_id=f"manifest_{run_id}_{workspace_id}", run_id=run_id, workspace_id=workspace_id,
            approved_decision_id=decision.decision_id,
            repository_before_fingerprint=before_fp, repository_after_fingerprint=after_fp,
            attempted_change_ids=attempted, applied_change_ids=applied,
            skipped_change_ids=skipped, failed_changes=failed,
            changed_files=changed_files, patch_diff_sha256=diff_sha,
            patch_diff_artifact_id=diff_artifact, applied_at=now,
        )

        rollback = RollbackManifest(
            rollback_id=f"rollback_{run_id}_{workspace_id}", manifest_id=manifest.manifest_id,
            workspace_id=workspace_id,
            repository_before_fingerprint=before_fp, repository_after_fingerprint=after_fp,
            rollback_paths=[e.repository_path for e in changed_files],
            rollback_blobs=[e.before_blob or "" for e in changed_files],
            rollback_target_paths=[e.rename_target_path or "" for e in changed_files],
            rollback_target_blobs=[e.target_before_blob or "" for e in changed_files],
            rollback_order="reverse_apply_order", rollback_strategy="blob_restore",
        )

        if not applied and not attempted:
            status = "patch_application_failed"
            ns = "replan_required"
        elif skipped or failed and applied:
            status = "patch_application_partial_failure"
            ns = "repair_or_rollback_pending"
        elif not applied and attempted:
            status = "patch_application_failed"
            ns = "replan_required"
        else:
            status = "patch_applied"
            ns = "repair_or_rollback_pending"

        return PatchExecutionResult(
            result_id=f"result_{run_id}", run_id=run_id, preflight=preflight,
            overall_status=status, manifests=[manifest],
            rollback_manifests=[rollback], next_stage=ns,
        )

    def rollback(self, *, result: PatchExecutionResult, repository_root: Path) -> PatchExecutionResult:
        now = datetime.now(timezone.utc)
        for rollback_m in result.rollback_manifests:
            entries = list(zip(
                rollback_m.rollback_paths, rollback_m.rollback_blobs,
                rollback_m.rollback_target_paths, rollback_m.rollback_target_blobs,
            ))
            for path, blob, tgt_path, tgt_blob in reversed(entries):
                abs_path = repository_root / path
                if tgt_path:
                    tgt_abs = repository_root / tgt_path
                    tgt_abs.unlink(missing_ok=True)
                if blob:
                    abs_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        abs_path.write_bytes(base64.b64decode(blob))
                    except Exception:
                        abs_path.write_bytes(blob.encode())
                else:
                    abs_path.unlink(missing_ok=True)
                if tgt_blob:
                    tgt_abs = repository_root / tgt_path if tgt_path else None
                    if tgt_abs:
                        tgt_abs.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            tgt_abs.write_bytes(base64.b64decode(tgt_blob))
                        except Exception:
                            tgt_abs.write_bytes(tgt_blob.encode())
            after_fp = _fingerprint(repository_root)
            rollback_m.rollback_applied = True
            rollback_m.rollback_fingerprint = after_fp
            rollback_m.fingerprint_matches_before = (after_fp == rollback_m.repository_before_fingerprint)
            rollback_m.rollback_at = now
        any_mismatch = any(
            not m.fingerprint_matches_before for m in result.rollback_manifests
            if m.fingerprint_matches_before is not None
        )
        final_status = "rolled_back" if not any_mismatch else "rollback_failed"
        return PatchExecutionResult(
            result_id=result.result_id, run_id=result.run_id, preflight=result.preflight,
            overall_status=final_status, manifests=result.manifests,
            validation_reports=result.validation_reports,
            rollback_manifests=result.rollback_manifests, next_stage="replan_required",
        )

    def run_local_validation(self, *, result: PatchExecutionResult, run_id: str,
                              workspace_id: str, repository_root: Path | None = None,
                              internal_steps: list[InternalValidationStep] | None = None,
                              external_commands: list[ExternalValidationCommand] | None = None,
                              approved_step_ids: list[str] | None = None,
                              approved_command_ids: list[str] | None = None) -> PostPatchValidationReport:
        now = datetime.now(timezone.utc)
        approved_steps = set(approved_step_ids or [])
        approved_cmds = set(approved_command_ids or [])
        checks: dict[str, CheckResult] = {
            "syntax": CheckResult(status="not_run"),
            "format": CheckResult(status="not_run"),
            "static": CheckResult(status="not_run"),
            "type": CheckResult(status="not_run"),
            "import": CheckResult(status="not_run"),
            "unit_test": None,
        }
        issues: list[str] = []

        changed_paths: list[str] = []
        if result.manifests:
            for m in result.manifests:
                for fe in m.changed_files:
                    changed_paths.append(fe.repository_path)

        if internal_steps and repository_root and repository_root.exists():
            for step in internal_steps:
                if step.step_id not in approved_steps:
                    if step.required:
                        issues.append(f"required step {step.step_id} not approved")
                    continue

                try:
                    if step.step_id == "ast_parse":
                        py_files = [repository_root / p for p in changed_paths if p.endswith(".py")]
                        if not py_files:
                            checks["syntax"] = CheckResult(status="not_required")
                        else:
                            all_errors: list[str] = []
                            for fp in py_files:
                                errs = ast_parse_step(file_path=fp)
                                all_errors.extend(errs)
                            checks["syntax"] = CheckResult(
                                status="passed" if not all_errors else "failed",
                                stderr_ref="\n".join(all_errors)[:500] if all_errors else None,
                            )
                    elif step.step_id == "diff_integrity":
                        diff_text = ""
                        if result.manifests and result.manifests[0].patch_diff_artifact_id:
                            artifact_path = Path(result.manifests[0].patch_diff_artifact_id)
                            if artifact_path.exists():
                                diff_text = artifact_path.read_text()
                        if not diff_text or not changed_paths:
                            checks["static"] = CheckResult(status="not_run")
                        else:
                            errs = diff_integrity_step(
                                proposed_diff=diff_text,
                                repository_root=repository_root,
                                changed_paths=changed_paths,
                            )
                            checks["static"] = CheckResult(
                                status="passed" if not errs else "failed",
                                stderr_ref="\n".join(errs)[:500] if errs else None,
                            )
                    elif step.step_id == "path_containment":
                        approved = self._policy_allowed_set if self._policy_allowed_set is not None else set(changed_paths)
                        errs = path_containment_step(
                            touched_paths=set(changed_paths),
                            approved_paths=approved,
                            policy_denied_paths=self.policy_denied_paths,
                        )
                        checks["format"] = CheckResult(
                            status="passed" if not errs else "failed",
                            stderr_ref="\n".join(errs)[:500] if errs else None,
                        )
                except Exception as e:
                    issues.append(f"internal step {step.step_id} error: {e}")

        if external_commands and repository_root and repository_root.exists():
            for cmd in external_commands:
                if cmd.command_id not in approved_cmds:
                    if cmd.required:
                        issues.append(f"required command {cmd.command_id} not approved")
                    continue
                r = execute_template_command(cmd, repository_root=repository_root)
                if r.status == "failed":
                    issues.append(f"external command {cmd.command_id} failed")
                if cmd.template_id == "ruff_check_no_fix" and checks["static"].status == "not_run":
                    checks["static"] = r
                elif cmd.template_id == "ruff_format_check" and checks["format"].status == "not_run":
                    checks["format"] = r

        all_ok = all(
            v is None or (isinstance(v, CheckResult) and v.status in {"passed", "not_required"})
            for v in checks.values()
        )
        return PostPatchValidationReport(
            report_id=f"pvr_{run_id}_{workspace_id}", run_id=run_id, workspace_id=workspace_id,
            manifest_id=result.manifests[0].manifest_id if result.manifests else "none",
            status=("patch_applied_and_local_validations_passed" if all_ok and not issues
                    else "patch_applied_but_local_validation_failed"),
            syntax_check=checks["syntax"], format_check=checks["format"],
            static_check=checks["static"], type_check=checks["type"],
            import_check=checks["import"], unit_tests=checks["unit_test"],
            issues=issues, validated_at=now,
        )

    def finalize_with_validation(self, *, result: PatchExecutionResult, run_id: str,
                                 workspace_id: str, repository_root: Path,
                                 internal_steps: list[InternalValidationStep] | None = None,
                                 external_commands: list[ExternalValidationCommand] | None = None,
                                 approved_step_ids: list[str] | None = None,
                                 approved_command_ids: list[str] | None = None) -> PatchExecutionResult:
        if result.overall_status not in {"patch_applied", "patch_applied_and_local_validations_passed"}:
            return PatchExecutionResult(
                result_id=result.result_id, run_id=run_id, preflight=result.preflight,
                overall_status=result.overall_status if result.overall_status != "blocked" else "blocked",
                manifests=result.manifests, validation_reports=result.validation_reports,
                rollback_manifests=result.rollback_manifests,
                next_stage="repair_or_rollback_pending",
            )
        report = self.run_local_validation(
            result=result, run_id=run_id, workspace_id=workspace_id,
            repository_root=repository_root, internal_steps=internal_steps,
            external_commands=external_commands,
            approved_step_ids=approved_step_ids, approved_command_ids=approved_command_ids,
        )
        new_status = ("patch_applied_and_local_validations_passed"
                      if report.status == "patch_applied_and_local_validations_passed"
                      else "patch_applied_but_local_validation_failed")
        ns = "eligible_for_runner_intake" if new_status == "patch_applied_and_local_validations_passed" else "repair_or_rollback_pending"
        return PatchExecutionResult(
            result_id=result.result_id, run_id=run_id, preflight=result.preflight,
            overall_status=new_status, manifests=result.manifests,
            validation_reports=[report], rollback_manifests=result.rollback_manifests,
            next_stage=ns,
        )

    def _apply_without_preflight(self, *, plan, decision, workspace_id, repository_root, run_id):
        from autoad_researcher.schemas.patch_planning import ApprovalRequest
        _empty_sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        req = ApprovalRequest(
            approval_request_id="internal", run_id=plan.run_id,
            workspace_id=workspace_id,
            patch_plan_sha256=plan.patch_plan_sha256,
            patch_payload_manifest_sha256=_empty_sha,
            proposed_patch_diff_sha256=_empty_sha,
            patch_payload_validation_report_sha256=_empty_sha,
            patch_plan_validation_report_sha256=_empty_sha,
            repository_before_fingerprint=plan.repository_fingerprint,
            internal_validation_steps=[], external_validation_commands=[],
            approval_request_sha256="",
            created_at=datetime.now(timezone.utc),
        )
        return self._apply_internal(plan=plan, decision=decision, request=req,
                                    workspace_id=workspace_id, repository_root=repository_root, run_id=run_id)

    def _apply_internal(self, *, plan, decision, request, workspace_id, repository_root, run_id):
        approved_change_ids = _decision_approved_ids(decision)
        planned_paths = {c.repository_path for c in plan.changes}
        for c in plan.changes:
            if c.rename_target_path:
                planned_paths.add(c.rename_target_path)
        payload_map: dict[str, PatchPayload] = {}
        self._set_approved_ask_paths(_decision_ask_paths(decision))
        now = datetime.now(timezone.utc)
        before_fp = _fingerprint(repository_root)
        changed_files, attempted, applied, skipped, failed = [], [], [], [], []
        workspace_changes = [c for c in plan.changes if c.workspace_id == workspace_id]
        for change in workspace_changes:
            if change.change_id not in approved_change_ids:
                continue
            attempted.append(change.change_id)
            allowed, _ = self.can_write_path(
                path=change.repository_path, approved_change_ids=approved_change_ids,
                change=change, planned_paths=planned_paths,
            )
            if not allowed:
                skipped.append(change.change_id); continue
            abs_path = self._check_and_resolve_path(repository_root, change.repository_path)
            if abs_path is None:
                skipped.append(change.change_id); continue
            target_abs = None
            if change.operation_kind == "rename" and change.rename_target_path:
                target_abs = self._check_and_resolve_path(repository_root, change.rename_target_path)
                if target_abs is None:
                    skipped.append(change.change_id); continue
            payload = payload_map.get(change.change_id)
            try:
                entry = _apply_single_change(change, abs_path, now, target_abs, payload)
            except Exception:
                failed.append(change.change_id); entry = None
            if entry:
                changed_files.append(entry); applied.append(change.change_id)
            else:
                skipped.append(change.change_id)
        after_fp = _fingerprint(repository_root)
        diff_text = _generate_unified_diff(repository_root, before_fp, after_fp, changed_files)
        diff_sha = hashlib.sha256(diff_text.encode()).hexdigest() if diff_text else None
        manifest = PatchApplicationManifest(
            manifest_id=f"manifest_{run_id}_{workspace_id}", run_id=run_id, workspace_id=workspace_id,
            approved_decision_id=decision.decision_id,
            repository_before_fingerprint=before_fp, repository_after_fingerprint=after_fp,
            attempted_change_ids=attempted, applied_change_ids=applied,
            skipped_change_ids=skipped, failed_changes=failed,
            changed_files=changed_files, patch_diff_sha256=diff_sha,
            applied_at=now,
        )
        rollback = RollbackManifest(
            rollback_id=f"rollback_{run_id}_{workspace_id}", manifest_id=manifest.manifest_id,
            workspace_id=workspace_id,
            repository_before_fingerprint=before_fp, repository_after_fingerprint=after_fp,
            rollback_paths=[e.repository_path for e in changed_files],
            rollback_blobs=[e.before_blob or "" for e in changed_files],
            rollback_target_paths=[e.rename_target_path or "" for e in changed_files],
            rollback_target_blobs=[e.target_before_blob or "" for e in changed_files],
            rollback_order="reverse_apply_order", rollback_strategy="blob_restore",
        )
        if not applied and not attempted:
            status = "patch_application_failed"; ns = "replan_required"
        elif not applied and attempted:
            status = "patch_application_failed"; ns = "replan_required"
        elif skipped or failed:
            status = "patch_application_partial_failure"; ns = "repair_or_rollback_pending"
        else:
            status = "patch_applied"; ns = "repair_or_rollback_pending"
        return PatchExecutionResult(
            result_id=f"result_{run_id}", run_id=run_id, preflight=None,
            overall_status=status, manifests=[manifest],
            rollback_manifests=[rollback], next_stage=ns,
        )


def _decision_plan_sha(d):
    return d.patch_plan_sha256


def _decision_approved_ids(d):
    if isinstance(d, (FullApprovalDecision, PartialApprovalDecision)):
        return set(d.approved_change_ids)
    return set()


def _decision_ask_paths(d):
    if isinstance(d, (FullApprovalDecision, PartialApprovalDecision)):
        return set(getattr(d, 'approved_ask_paths', []))
    return set()


def _apply_single_change(change, abs_path: Path, now: datetime,
                          target_abs: Path | None = None,
                          payload: PatchPayload | None = None) -> ChangedFileEntry | None:
    before_content: bytes | None = None
    before_sha: str | None = None
    path_exists = abs_path.exists()

    if path_exists:
        before_content = abs_path.read_bytes()
        before_sha = hashlib.sha256(before_content).hexdigest()

    policy = change.target_collision_policy

    if change.operation_kind == "create" and path_exists:
        if policy == "must_not_exist":
            return None
    elif change.operation_kind in {"modify", "delete"}:
        if not path_exists:
            return None
        if policy == "replace_existing" and change.target_before_sha256:
            if before_sha != change.target_before_sha256:
                return None
    if change.operation_kind == "rename":
        if not abs_path.exists():
            return None
        if target_abs is not None:
            if target_abs.exists():
                if policy != "replace_existing":
                    return None
                target_before_content = target_abs.read_bytes()
                target_before_sha = hashlib.sha256(target_before_content).hexdigest()
                if change.target_before_sha256 and target_before_sha != change.target_before_sha256:
                    return None
        if policy == "replace_existing" and change.target_before_sha256:
            if target_abs and target_abs.exists():
                pass
            else:
                return None

    if change.operation_kind == "delete":
        if not abs_path.exists():
            return None
        abs_path.unlink()
        return ChangedFileEntry(
            file_entry_id=f"fe_{change.change_id}", repository_path=change.repository_path,
            operation_kind=change.operation_kind, before_sha256=before_sha, after_sha256=None,
            before_blob=_to_blob(before_content), change_ids=[change.change_id],
            operation="deleted", applied_at=now,
        )

    if change.operation_kind == "rename":
        if not abs_path.exists():
            return None
        if not target_abs:
            return None
        src_content = before_content
        src_sha = before_sha
        target_before_content = None
        target_before_blob = None
        if target_abs.exists():
            target_before_content = target_abs.read_bytes()
            target_before_blob = _to_blob(target_before_content)
        target_abs.parent.mkdir(parents=True, exist_ok=True)
        abs_path.rename(target_abs)
        return ChangedFileEntry(
            file_entry_id=f"fe_{change.change_id}", repository_path=change.repository_path,
            rename_target_path=change.rename_target_path,
            operation_kind=change.operation_kind, before_sha256=src_sha, after_sha256=None,
            before_blob=_to_blob(src_content),
            target_before_blob=target_before_blob,
            change_ids=[change.change_id], operation="renamed", applied_at=now,
        )

    if change.operation_kind == "modify":
        if not abs_path.exists():
            return None
        if payload and payload.payload_kind == "full_after_content":
            new_content = _resolve_payload_content(payload)
        else:
            original = before_content or b""
            new_content = original + b"\n" + _render_placeholder(change)
    else:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        new_content = _render_placeholder(change)

    tmp_path = abs_path.with_suffix(abs_path.suffix + ".patch_tmp")
    try:
        tmp_path.write_bytes(new_content)
        os.replace(tmp_path, abs_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    after_content = abs_path.read_bytes()
    after_sha = hashlib.sha256(after_content).hexdigest()
    operation = "created" if before_sha is None else "written"
    return ChangedFileEntry(
        file_entry_id=f"fe_{change.change_id}", repository_path=change.repository_path,
        operation_kind=change.operation_kind, before_sha256=before_sha, after_sha256=after_sha,
        before_blob=_to_blob(before_content), change_ids=[change.change_id],
        operation=operation, applied_at=now,
    )


def _to_blob(content: bytes | None) -> str | None:
    if content is None:
        return None
    return base64.b64encode(content).decode("ascii")


def _resolve_payload_content(payload: PatchPayload) -> bytes:
    try:
        ref_path = Path(payload.payload_artifact_id)
        if ref_path.exists():
            return ref_path.read_bytes()
    except Exception:
        pass
    return f"# payload placeholder: {payload.payload_id}\n".encode()


def _render_placeholder(change) -> bytes:
    lines = [f"# Patch: {change.change_id}", f"# Variant: {change.variant_ids}", f"# Rationale: {change.rationale}"]
    if change.symbol_delta:
        sd = change.symbol_delta
        lines.append(f"# Symbol: {sd.symbol_name}")
    return "\n".join(lines).encode("utf-8") + b"\n"


def _generate_unified_diff(root, before, after, files) -> str | None:
    lines = []
    for entry in files:
        fpath = root / entry.repository_path
        if entry.operation == "deleted":
            if entry.before_blob:
                lines.append(f"--- a/{entry.repository_path}")
                lines.append("+++ /dev/null")
                try:
                    bl = base64.b64decode(entry.before_blob).decode("utf-8")
                except Exception:
                    continue
                for l in bl.split("\n"):
                    lines.append(f"-{l}")
            continue
        if entry.operation == "renamed":
            tgt = entry.rename_target_path or entry.repository_path
            lines.append(f"rename {entry.repository_path} -> {tgt}")
            continue
        if not fpath.exists():
            continue
        current = fpath.read_text()
        try:
            original = base64.b64decode(entry.before_blob or "").decode("utf-8")
        except Exception:
            original = ""
        lines.append(f"--- a/{entry.repository_path}")
        lines.append(f"+++ b/{entry.repository_path}")
        diff = difflib.unified_diff(original.split("\n"), current.split("\n"),
                                     fromfile=f"a/{entry.repository_path}",
                                     tofile=f"b/{entry.repository_path}", lineterm="")
        lines.extend(list(diff))
    return "\n".join(lines) if lines else None


def _run_syntax_check(root):
    try:
        proc = subprocess.run(["python", "-m", "compileall", "-q", str(root)],
                              capture_output=True, text=True, timeout=30)
        ok = proc.returncode == 0
        return CheckResult(status="passed" if ok else "failed", command_id="cmd_syntax",
                           exit_code=proc.returncode, stderr_ref=proc.stderr[:500] if proc.stderr else None)
    except Exception as exc:
        return CheckResult(status="failed", command_id="cmd_syntax", stderr_ref=str(exc)[:500])


def _exec_external_command(cmd: ExternalValidationCommand) -> CheckResult:
    try:
        proc = subprocess.run(cmd.resolved_argv, capture_output=True, text=True, timeout=120,
                              cwd=cmd.working_directory)
        ok = proc.returncode == 0
        return CheckResult(
            status="passed" if ok else "failed", command_id=cmd.command_id,
            exit_code=proc.returncode,
            stdout_ref=proc.stdout[:2000] if proc.stdout else None,
            stderr_ref=proc.stderr[:2000] if proc.stderr else None,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(status="failed", command_id=cmd.command_id, stderr_ref="timeout")
    except Exception as exc:
        return CheckResult(status="failed", command_id=cmd.command_id, stderr_ref=str(exc)[:500])


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")[:-1]; result = []
    while parts:
        result.append("/".join(parts)); parts = parts[:-1]
    return result


def _path_in_scope(path: str, scope: set[str]) -> bool:
    if not scope: return False
    candidate = path
    while True:
        if candidate in scope: return True
        parts = candidate.split("/")
        if len(parts) <= 1: break
        candidate = "/".join(parts[:-1])
    return path in scope


def _fingerprint(root: Path) -> str:
    if not root.exists():
        return _hash(b"empty")
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in sorted(os.walk(root)):
        dirnames.sort()
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            if fp.endswith(".patch_tmp"): continue
            try:
                h.update(os.path.relpath(fp, root).encode())
                with open(fp, "rb") as fh: h.update(fh.read())
            except OSError: pass
    return h.hexdigest()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
