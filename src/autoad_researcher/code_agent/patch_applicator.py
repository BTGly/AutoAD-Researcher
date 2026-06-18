"""ControlledPatchApplicator — Step 3.7 controlled patch application.

Applies approved changes with: preflight checks, full layered permission,
fail-closed result tracking, reverse-order rollback, rename support,
unified diff generation, and approved validation command execution.
"""

import difflib
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.schemas.patch_planning import (
    ApprovalDecision,
    ApprovalRequest,
    ChangedFileEntry,
    CheckResult,
    PatchApplicationManifest,
    PatchApplicationPreflightResult,
    PatchExecutionResult,
    PostPatchValidationReport,
    PlannedRepositoryChange,
    RepositoryChangePlan,
    RollbackManifest,
    ValidationCommand,
    compute_canonical_plan_sha256,
)


class ControlledPatchApplicator:
    def __init__(
        self,
        *,
        policy_denied_paths: set[str] | None = None,
        policy_allowed_paths: set[str] | None = None,
        policy_ask_paths: set[str] | None = None,
    ):
        self.policy_denied_paths = policy_denied_paths or set()
        self._policy_allowed_set = policy_allowed_paths
        self._policy_ask_set = policy_ask_paths
        self.policy_ask_paths = policy_ask_paths or set()

    def can_write_path(
        self, *, path: str, approved_change_ids: set[str],
        change: PlannedRepositoryChange, planned_paths: set[str],
    ) -> tuple[bool, str]:
        """Full layered write permission. Default deny."""
        if change.change_id not in approved_change_ids:
            return False, f"change_id {change.change_id} not approved"
        if path in self.policy_denied_paths:
            return False, f"path {path} is policy-denied"
        for ancestor in _ancestors(path):
            if ancestor in self.policy_denied_paths:
                return False, f"ancestor {ancestor} of {path} is policy-denied"
        if path not in planned_paths:
            return False, f"path {path} not in planned paths"
        if path in self.policy_ask_paths:
            return False, f"path {path} requires explicit ask approval"
        if self._policy_allowed_set is not None and not _path_is_in_scope(path, self._policy_allowed_set):
            return False, f"path {path} not in policy-allowed scope"
        return True, "allowed"

    def _check_and_resolve_path(self, repository_root: Path, path_key: str) -> Path | None:
        try:
            candidate = (repository_root / path_key).resolve()
            root = repository_root.resolve()
            if not str(candidate).startswith(str(root) + os.sep) and candidate != root:
                return None
            return candidate
        except (ValueError, OSError):
            return None

    def run_preflight(
        self, *, plan: RepositoryChangePlan, request: ApprovalRequest,
        decision: ApprovalDecision, workspace_id: str,
        repository_root: Path, run_id: str,
        validation_report_status_passed: bool = False,
    ) -> PatchApplicationPreflightResult:
        issues: list[str] = []
        canonical = compute_canonical_plan_sha256(plan)
        plan_sha_valid = canonical == plan.plan_sha256
        if not plan_sha_valid:
            issues.append("plan_sha256 mismatch: computed != stored")
        decision_sha_valid = decision.approved_patch_plan_sha256 == plan.plan_sha256
        if not decision_sha_valid:
            issues.append("decision SHA != plan SHA")
        request_sha_valid = request.patch_plan_sha256 == plan.plan_sha256
        if not request_sha_valid:
            issues.append("request SHA != plan SHA")
        actual = _fingerprint_directory(repository_root)
        fp_match = actual == plan.repository_fingerprint
        if not fp_match:
            issues.append(f"repository fingerprint mismatch: actual {actual[:16]} != plan {plan.repository_fingerprint[:16]}")
        rid_match = run_id == plan.run_id
        if not rid_match:
            issues.append("run_id mismatch")
        ws_exists = not plan.workspace_plans or any(w.workspace_id == workspace_id for w in plan.workspace_plans)
        ready = all([plan_sha_valid, decision_sha_valid, request_sha_valid, fp_match, rid_match, ws_exists, validation_report_status_passed])
        return PatchApplicationPreflightResult(
            preflight_id=f"preflight_{run_id}_{workspace_id}",
            run_id=run_id, workspace_id=workspace_id,
            plan_sha_valid=plan_sha_valid,
            decision_sha_valid=decision_sha_valid,
            request_sha_valid=request_sha_valid,
            repository_fingerprint_match=fp_match,
            run_id_match=rid_match,
            workspace_exists_in_plan=ws_exists,
            validation_report_passed=validation_report_status_passed,
            ready=ready, issues=issues,
        )

    def apply_patch(
        self, *, plan: RepositoryChangePlan, decision: ApprovalDecision,
        workspace_id: str, repository_root: Path, run_id: str,
        request: ApprovalRequest | None = None,
        validation_report_passed: bool = False,
        skip_preflight: bool = False,
    ) -> PatchExecutionResult:
        approved_change_ids = set(decision.approved_change_ids)
        planned_paths = {c.repository_path for c in plan.changes}
        for c in plan.changes:
            if c.rename_target_path:
                planned_paths.add(c.rename_target_path)

        now = datetime.now(timezone.utc)
        before_fp = _fingerprint_directory(repository_root)

        preflight = None
        if not skip_preflight:
            preflight = self.run_preflight(
                plan=plan, request=request or _dummy_request(plan),
                decision=decision, workspace_id=workspace_id,
                repository_root=repository_root, run_id=run_id,
                validation_report_status_passed=validation_report_passed,
            )
            if not preflight.ready:
                return PatchExecutionResult(
                    result_id=f"result_{run_id}", run_id=run_id,
                    preflight=preflight, overall_status="blocked",
                    next_stage="replan_required",
                )

        changed_files: list[ChangedFileEntry] = []
        attempted: list[str] = []
        applied: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

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
            if change.change_kind == "rename" and change.rename_target_path:
                target_abs = self._check_and_resolve_path(repository_root, change.rename_target_path)
                if target_abs is None:
                    skipped.append(change.change_id)
                    continue

            entry = _apply_single_change(change, abs_path, now, target_abs)
            if entry:
                changed_files.append(entry)
                applied.append(change.change_id)
            else:
                skipped.append(change.change_id)

        after_fp = _fingerprint_directory(repository_root)

        diff_text = _generate_unified_diff(repository_root, before_fp, after_fp, changed_files)
        diff_sha = hashlib.sha256(diff_text.encode("utf-8")).hexdigest() if diff_text else None

        manifest = PatchApplicationManifest(
            manifest_id=f"manifest_{run_id}_{workspace_id}",
            run_id=run_id, workspace_id=workspace_id,
            approved_decision_id=decision.decision_id,
            repository_before_fingerprint=before_fp,
            repository_after_fingerprint=after_fp,
            attempted_change_ids=attempted,
            applied_change_ids=applied,
            skipped_change_ids=skipped,
            failed_changes=failed,
            changed_files=changed_files,
            patch_diff_sha256=diff_sha,
            applied_at=now,
        )

        rollback = RollbackManifest(
            rollback_id=f"rollback_{run_id}_{workspace_id}",
            manifest_id=manifest.manifest_id, workspace_id=workspace_id,
            repository_before_fingerprint=before_fp,
            repository_after_fingerprint=after_fp,
            rollback_paths=[e.repository_path for e in changed_files],
            rollback_blobs=[e.before_blob or "" for e in changed_files],
            rollback_order="reverse_apply_order",
            rollback_strategy="blob_restore",
        )

        if not applied and not attempted:
            status = "patch_application_failed"
            next_stage = "replan_required"
        elif skipped and applied:
            status = "patch_application_partial_failure"
            next_stage = "repair_or_rollback_pending"
        elif not applied and attempted:
            status = "patch_application_failed"
            next_stage = "replan_required"
        else:
            status = "patch_applied"
            next_stage = "repair_or_rollback_pending"

        return PatchExecutionResult(
            result_id=f"result_{run_id}", run_id=run_id,
            preflight=preflight, overall_status=status,
            manifests=[manifest],
            rollback_manifests=[rollback],
            next_stage=next_stage,
        )

    def rollback(self, *, result: PatchExecutionResult, repository_root: Path) -> PatchExecutionResult:
        now = datetime.now(timezone.utc)
        for rollback_m in result.rollback_manifests:
            targets = list(zip(rollback_m.rollback_paths, rollback_m.rollback_blobs))
            for path, blob in reversed(targets):
                abs_path = repository_root / path
                if blob:
                    abs_path.parent.mkdir(parents=True, exist_ok=True)
                    abs_path.write_bytes(blob.encode("utf-8"))
                else:
                    abs_path.unlink(missing_ok=True)
            after_fp = _fingerprint_directory(repository_root)
            rollback_m.rollback_applied = True
            rollback_m.rollback_fingerprint = after_fp
            rollback_m.fingerprint_matches_before = (after_fp == rollback_m.repository_before_fingerprint)
            rollback_m.rollback_at = now
        return PatchExecutionResult(
            result_id=result.result_id, run_id=result.run_id,
            preflight=result.preflight, overall_status="rolled_back",
            manifests=result.manifests, validation_reports=result.validation_reports,
            rollback_manifests=result.rollback_manifests,
            next_stage="replan_required",
        )

    def run_local_validation(
        self, *, result: PatchExecutionResult, run_id: str,
        workspace_id: str, repository_root: Path | None = None,
        commands: list[ValidationCommand] | None = None,
        approved_command_ids: list[str] | None = None,
    ) -> PostPatchValidationReport:
        now = datetime.now(timezone.utc)
        issues: list[str] = []
        approved = set(approved_command_ids or [])

        syntax = CheckResult(status="not_run")
        fmt = CheckResult(status="not_run")
        static = CheckResult(status="not_run")
        type_ck = CheckResult(status="not_run")
        import_ck = CheckResult(status="not_run")

        if repository_root and repository_root.exists():
            syntax = _run_syntax_check(repository_root, now)

        if commands and approved:
            for cmd in commands:
                if cmd.command_id not in approved:
                    continue
                r = _exec_validation_command(cmd)
                if cmd.label.lower().startswith("format"):
                    fmt = r
                elif cmd.label.lower().startswith("static") or "lint" in cmd.label.lower():
                    static = r
                elif "type" in cmd.label.lower():
                    type_ck = r
                elif "import" in cmd.label.lower():
                    import_ck = r

        all_passed = all(
            c.status in {"passed", "not_required"}
            for c in [syntax, fmt, static, type_ck, import_ck]
        )
        return PostPatchValidationReport(
            report_id=f"pvr_{run_id}_{workspace_id}",
            run_id=run_id, workspace_id=workspace_id,
            manifest_id=result.manifests[0].manifest_id if result.manifests else "none",
            status=(
                "patch_applied_and_local_validations_passed" if all_passed
                else "patch_applied_but_local_validation_failed"
            ),
            syntax_check=syntax, format_check=fmt, static_check=static,
            type_check=type_ck, import_check=import_ck,
            issues=issues, validated_at=now,
        )

    def finalize_with_validation(
        self, *, result: PatchExecutionResult, run_id: str,
        workspace_id: str, repository_root: Path,
        commands: list[ValidationCommand] | None = None,
        approved_command_ids: list[str] | None = None,
    ) -> PatchExecutionResult:
        report = self.run_local_validation(
            result=result, run_id=run_id, workspace_id=workspace_id,
            repository_root=repository_root,
            commands=commands, approved_command_ids=approved_command_ids,
        )
        new_status = (
            "patch_applied_and_local_validations_passed"
            if report.status == "patch_applied_and_local_validations_passed"
            else "patch_applied_but_local_validation_failed"
        )
        ns = "eligible_for_runner_intake" if new_status == "patch_applied_and_local_validations_passed" else "repair_or_rollback_pending"
        return PatchExecutionResult(
            result_id=result.result_id, run_id=run_id,
            preflight=result.preflight, overall_status=new_status,
            manifests=result.manifests, validation_reports=[report],
            rollback_manifests=result.rollback_manifests, next_stage=ns,
        )


def _apply_single_change(
    change, abs_path: Path, now: datetime, target_abs: Path | None = None,
) -> ChangedFileEntry | None:
    before_content: bytes | None = None
    before_sha: str | None = None
    if abs_path.exists():
        before_content = abs_path.read_bytes()
        before_sha = hashlib.sha256(before_content).hexdigest()

    if change.change_kind == "delete":
        if abs_path.exists():
            abs_path.unlink()
        return ChangedFileEntry(
            file_entry_id=f"fe_{change.change_id}", repository_path=change.repository_path,
            change_kind=change.change_kind, before_sha256=before_sha, after_sha256=None,
            before_blob=_bytes_to_blob(before_content),
            change_ids=[change.change_id], operation="deleted", applied_at=now,
        )

    if change.change_kind == "rename" and target_abs:
        if not abs_path.exists():
            return None
        src_content = abs_path.read_bytes()
        src_sha = hashlib.sha256(src_content).hexdigest() if src_content else None
        target_abs.parent.mkdir(parents=True, exist_ok=True)
        abs_path.rename(target_abs)
        return ChangedFileEntry(
            file_entry_id=f"fe_{change.change_id}", repository_path=change.repository_path,
            change_kind=change.change_kind, before_sha256=src_sha, after_sha256=None,
            before_blob=_bytes_to_blob(src_content),
            change_ids=[change.change_id], operation="renamed", applied_at=now,
        )

    if change.change_kind in {"modify", "configuration_only", "test_only"} and abs_path.exists():
        original = before_content or b""
        placeholder = _render_placeholder(change)
        new_content = original + b"\n" + placeholder
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
        change_kind=change.change_kind, before_sha256=before_sha, after_sha256=after_sha,
        before_blob=_bytes_to_blob(before_content),
        change_ids=[change.change_id], operation=operation, applied_at=now,
    )


def _render_placeholder(change) -> bytes:
    lines = [
        f"# Patch: {change.change_id}",
        f"# Variant: {change.variant_ids}",
        f"# Rationale: {change.rationale}",
    ]
    if change.symbol_delta:
        sd = change.symbol_delta
        lines.append(f"# Symbol: {sd.symbol_name}")
        if sd.current_responsibility:
            lines.append(f"# Before: {sd.current_responsibility}")
        if sd.planned_responsibility:
            lines.append(f"# After: {sd.planned_responsibility}")
    return "\n".join(lines).encode("utf-8") + b"\n"


def _bytes_to_blob(content: bytes | None) -> str | None:
    if content is None:
        return None
    return content.decode("utf-8", errors="replace")


def _generate_unified_diff(root: Path, before: str, after: str, files: list[ChangedFileEntry]) -> str | None:
    lines: list[str] = []
    for entry in files:
        fpath = root / entry.repository_path
        if entry.operation == "deleted":
            if entry.before_blob:
                lines.append(f"--- a/{entry.repository_path}")
                lines.append("+++ /dev/null")
                bl = entry.before_blob.split("\n")
                for l in bl:
                    lines.append(f"-{l}")
            continue
        if entry.operation == "renamed":
            lines.append(f"rename {entry.repository_path} -> {entry.repository_path}")
            continue
        if not fpath.exists():
            continue
        current = fpath.read_text()
        original = entry.before_blob or ""
        lines.append(f"--- a/{entry.repository_path}")
        lines.append(f"+++ b/{entry.repository_path}")
        diff = difflib.unified_diff(original.split("\n"), current.split("\n"),
                                     fromfile=f"a/{entry.repository_path}",
                                     tofile=f"b/{entry.repository_path}", lineterm="")
        lines.extend(list(diff))
    return "\n".join(lines) if lines else None


def _run_syntax_check(root: Path, now: datetime) -> CheckResult:
    try:
        proc = subprocess.run(
            ["python", "-m", "compileall", "-q", str(root)],
            capture_output=True, text=True, timeout=30,
        )
        ok = proc.returncode == 0
        return CheckResult(status="passed" if ok else "failed", command_id="cmd_syntax",
                           exit_code=proc.returncode, stderr_ref=proc.stderr[:500] if proc.stderr else None)
    except Exception as exc:
        return CheckResult(status="failed", command_id="cmd_syntax", stderr_ref=str(exc)[:500])


def _exec_validation_command(cmd: ValidationCommand) -> CheckResult:
    try:
        proc = subprocess.run(cmd.argv, capture_output=True, text=True, timeout=cmd.timeout_seconds)
        ok = proc.returncode == cmd.expected_exit_code
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


def _dummy_request(plan: RepositoryChangePlan) -> ApprovalRequest:
    return ApprovalRequest(
        approval_request_id="dummy", run_id=plan.run_id,
        patch_plan_sha256=plan.plan_sha256,
        repository_before_fingerprint=plan.repository_fingerprint,
        validation_commands=[],
        created_at=datetime.now(timezone.utc),
    )


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    result = []
    while parts:
        result.append("/".join(parts))
        parts = parts[:-1]
    return result


def _path_is_in_scope(path: str, scope: set[str]) -> bool:
    if not scope:
        return False
    candidate = path
    while True:
        if candidate in scope:
            return True
        parts = candidate.split("/")
        if len(parts) <= 1:
            break
        candidate = "/".join(parts[:-1])
    return path in scope


def _fingerprint_directory(root: Path) -> str:
    if not root.exists():
        return _hash_hex(b"empty")
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in sorted(os.walk(root)):
        dirnames.sort()
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            if fp.endswith(".patch_tmp"):
                continue
            try:
                h.update(os.path.relpath(fp, root).encode())
                with open(fp, "rb") as fh:
                    h.update(fh.read())
            except OSError:
                pass
    return h.hexdigest()


def _hash_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
