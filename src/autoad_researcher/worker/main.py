#!/usr/bin/env python3
"""AutoAD V2 Worker — claims strict control-plane jobs and executes them.

Usage:
    uv run python -m autoad_researcher.worker.main
    uv run python -m autoad_researcher.worker.main --run-id run_xxx --once
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tarfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from autoad_researcher.core.control_plane import (
    ControlPlaneEventStore,
    CorruptAuditProjection,
    CorruptAuthoritativeStore,
    EventIdempotencyConflict,
    JobClaimFenceError,
    JobTransition,
    PipelineJobStore,
)
from autoad_researcher.core.control_plane.io import atomic_write_json
from autoad_researcher.core.control_plane.readiness import (
    materialize_claimed_experiment_prepare,
    repair_experiment_session_projection,
)
from autoad_researcher.core.control_plane.reconciliation import (
    reconcile_control_plane_events,
    reconcile_incomplete_terminal_attempts,
    reconcile_materialization_requests,
)
from autoad_researcher.core.control_plane.validate import (
    validate_authoritative_control_plane_invariants,
    validate_authoritative_store_syntax,
)

RUNS_ROOT = os.environ.get("AUTOAD_RUNS_ROOT", "runs")

WORKER_ID = f"worker_{os.getpid()}_{uuid4().hex}"


class _AuditWriter:
    """Keep non-authoritative audit corruption from blocking one run's jobs."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.store = ControlPlaneEventStore(run_dir)
        self.degraded = False
        try:
            self.store.read_since()
        except (CorruptAuditProjection, EventIdempotencyConflict) as exc:
            self._degrade(exc)

    def append_once(self, event_type: str, key: str, payload: dict[str, Any]) -> None:
        if self.degraded:
            return
        try:
            self.store.append_once(event_type, key, payload)
        except (CorruptAuditProjection, EventIdempotencyConflict) as exc:
            self._degrade(exc)

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.degraded:
            return
        try:
            self.store.append(event_type, payload)
        except CorruptAuditProjection as exc:
            self._degrade(exc)

    def _degrade(self, exc: Exception) -> None:
        self.degraded = True
        print(f"[worker] audit degraded for {self.run_dir.name}: {exc}", file=sys.stderr)
        try:
            atomic_write_json(
                self.run_dir / "events" / "audit_health.json",
                {
                    "schema_version": 1,
                    "status": "degraded",
                    "reason": str(exc),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as health_error:
            print(f"[worker] could not persist audit health: {health_error}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="AutoAD V2 Worker")
    parser.add_argument("--run-id", help="Process only this run (otherwise all)")
    parser.add_argument("--once", action="store_true", help="Process once and exit")
    parser.add_argument("--interval", type=int, default=3, help="Poll interval (seconds)")
    args = parser.parse_args()

    print(f"[worker] starting — runs_root={RUNS_ROOT}")

    while True:
        processed = 0
        runs_dir = Path(RUNS_ROOT)
        if not runs_dir.exists():
            time.sleep(args.interval)
            continue

        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            if args.run_id and run_dir.name != args.run_id:
                continue
            try:
                processed += _process_pending_jobs(run_dir, worker_id=WORKER_ID)
            except CorruptAuthoritativeStore as exc:
                print(f"[worker] authoritative store corrupt for {run_dir.name}: {exc}", file=sys.stderr)
            except Exception as exc:
                print(f"[worker] failed for {run_dir.name}: {exc}", file=sys.stderr)

        if processed:
            print(f"[worker] processed {processed} jobs")
        if args.once:
            break
        if not processed:
            print(f"[worker] no pending jobs, sleeping {args.interval}s")
        time.sleep(args.interval)

    print("[worker] done")


def _process_pending_jobs(run_dir: Path, *, worker_id: str = WORKER_ID) -> int:
    validate_authoritative_store_syntax(run_dir)
    store = PipelineJobStore(run_dir)
    audit = _AuditWriter(run_dir)
    processed = 0

    reconcile_incomplete_terminal_attempts(run_dir)
    reconcile_materialization_requests(run_dir)

    for result in store.reconcile_orphan_claims():
        audit.append_once(
            "job.claim_aborted",
            f"job.claim_aborted:{result.job_id}:attempt:{result.attempt_count}",
            result.model_dump(mode="json", exclude_none=True),
        )

    repair_experiment_session_projection(run_dir)
    validate_authoritative_control_plane_invariants(run_dir)

    for transition in store.requeue_expired():
        _append_job_transition(audit, transition)

    repair_experiment_session_projection(run_dir)

    dependency_transitions = store.reconcile_job_dependencies()
    for transition in dependency_transitions:
        _append_job_transition(audit, transition)
        processed += 1

    while claimed := store.claim_next(worker_id=worker_id):
        job = claimed.model_dump(mode="json", exclude_none=False)
        job_id = claimed.job_id
        job_type = claimed.job_type
        claim_token = claimed.claim_token
        if claim_token is None:
            raise CorruptAuthoritativeStore(f"claimed job {job_id} has no claim token")
        attempt_count = claimed.attempt_count
        print(f"[worker] running {job_type} ({job_id}) in {run_dir.name}")
        audit.append_once(
            "job.started",
            f"job.started:{job_id}:attempt:{attempt_count}",
            {"job_id": job_id, "job_type": job_type, "attempt_count": attempt_count},
        )

        success = False
        outputs: list[str] = []
        error_msg: str | None = None
        materialization_outcome = None
        try:
            if job_type == "experiment_prepare":
                materialization_outcome = materialize_claimed_experiment_prepare(run_dir, claimed)
                outputs = (
                    [materialization_outcome.readiness_path]
                    if materialization_outcome.readiness_path is not None
                    else []
                )
            elif job_type == "web_search":
                success = _run_web_search(run_dir, job)
            elif job_type == "web_fetch":
                success, outputs = _run_web_fetch(run_dir, job)
            elif job_type == "web_markitdown":
                success, outputs = _run_web_markitdown(run_dir, job)
            elif job_type == "git_clone":
                success, outputs = _run_git_clone(run_dir, job)
            elif job_type == "local_repo_unpack":
                success, outputs = _run_local_repo_unpack(run_dir, job)
            elif job_type == "local_repo_acquire":
                success, outputs = _run_local_repo_acquire(run_dir, job)
            elif job_type == "archive_unpack_classify":
                success, outputs = _run_archive_unpack_classify(run_dir, job)
            elif job_type == "document_markitdown":
                success, outputs = _run_document_markitdown(run_dir, job)
            elif job_type in {"paper_parse", "paper_parse_mineru"}:
                success, outputs = _run_paper_parse_mineru(run_dir, job)
            elif job_type == "paper_parse_markitdown":
                success, outputs = _run_paper_parse_markitdown(run_dir, job)
            elif job_type == "paper_summarize":
                success, outputs = _run_paper_summarize(run_dir, job)
            elif job_type in {"repo_analyze", "repo_summarize"}:
                success, outputs = _run_repo_analyze(run_dir, job)
            else:
                error_msg = f"unknown job_type: {job_type}"
        except Exception as exc:
            error_msg = str(exc)[:500]

        try:
            if materialization_outcome is not None:
                event_type = (
                    "job.completed"
                    if materialization_outcome.job_status == "completed"
                    else "job.stale_input"
                )
                audit.append_once(
                    event_type,
                    f"{event_type}:{job_id}:attempt:{attempt_count}",
                    {
                        "job_id": job_id,
                        "attempt_count": attempt_count,
                        "materialization_status": materialization_outcome.status,
                        "job_status": materialization_outcome.job_status,
                        "outputs": outputs,
                    },
                )
                if outputs:
                    audit.append_once(
                        "artifact.created",
                        f"artifact.created:{job_id}:attempt:{attempt_count}",
                        {"job_id": job_id, "paths": outputs},
                    )
                processed += 1
                continue
            if success and error_msg is None:
                store.complete(
                    job_id,
                    claim_token=claim_token,
                    expected_attempt_count=attempt_count,
                    outputs=outputs,
                )
                audit.append_once(
                    "job.completed",
                    f"job.completed:{job_id}:attempt:{attempt_count}",
                    {"job_id": job_id, "attempt_count": attempt_count, "outputs": outputs},
                )
                if outputs:
                    audit.append_once(
                        "artifact.created",
                        f"artifact.created:{job_id}:attempt:{attempt_count}",
                        {"job_id": job_id, "paths": outputs},
                    )
                    audit.append_once(
                        "evidence.updated",
                        f"evidence.updated:{job_id}:attempt:{attempt_count}",
                        {"job_id": job_id},
                    )
                audit.append("toast.success", {"message": f"{job_type} 完成"})
            else:
                error_msg = error_msg or _best_job_error(run_dir, job)
                store.fail(
                    job_id,
                    claim_token=claim_token,
                    expected_attempt_count=attempt_count,
                    error=error_msg,
                )
                audit.append_once(
                    "job.failed",
                    f"job.failed:{job_id}:attempt:{attempt_count}",
                    {
                        "job_id": job_id,
                        "job_type": job_type,
                        "source_id": claimed.source_id,
                        "attempt_count": attempt_count,
                        "error": error_msg,
                    },
                )
                audit.append("toast.error", {"message": f"{job_type} 失败：{error_msg}"})
        except JobClaimFenceError as exc:
            print(f"[worker] lost claim for {job_id}: {exc}", file=sys.stderr)
        processed += 1

    if not audit.degraded:
        try:
            reconcile_control_plane_events(run_dir)
        except (CorruptAuditProjection, EventIdempotencyConflict) as exc:
            audit._degrade(exc)

    return processed


def _append_job_transition(audit: _AuditWriter, transition: JobTransition) -> None:
    event_type = "job.lease_expired" if transition.reason == "lease_expired" else "job.failed"
    audit.append_once(
        event_type,
        f"{event_type}:{transition.job_id}:attempt:{transition.attempt_count}:reason:{transition.reason}",
        transition.model_dump(mode="json"),
    )


def _run_web_search(run_dir: Path, job: dict[str, Any]) -> bool:
    from autoad_researcher.assistant.material_subagents import run_material_discovery_subagent
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    request = {
        "request_id": job.get("job_id", ""),
        "kind": "web_search",
        "payload": payload,
        "user_message": payload.get("query") or job.get("source_id", ""),
        "evidence_role": job.get("evidence_role", "candidate_source_only"),
    }
    run_material_discovery_subagent(run_dir, request=request)
    return True


def _run_web_fetch(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = job.get("source_id", "")
    url = _find_source_url(run_dir, source_id)
    if not url:
        return False, []

    from autoad_researcher.tools.providers import SecureWebFetchProvider
    provider = SecureWebFetchProvider()
    result = provider.fetch(url)
    out_dir = run_dir / "sources" / source_id
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "raw.html"
    html_path.write_text(result.content, encoding="utf-8")
    return True, [str(html_path.relative_to(run_dir))]


def _run_web_markitdown(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = job.get("source_id", "")
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    input_rel = str(payload.get("input_path") or "")
    if not input_rel:
        input_rel = f"sources/{source_id}/raw.html"
    input_path = run_dir / input_rel
    output_path = run_dir / "sources" / source_id / "content.md"

    from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
    from autoad_researcher.tools.markitdown_adapter import convert_local_to_markdown

    result = convert_local_to_markdown(input_path, output_path, run_dir=run_dir)
    if not result.ok:
        _write_parse_error(run_dir, source_id, "markitdown", result.error or "markitdown failed")
        return False, []
    artifact_path = result.output_paths[0]
    summary = _markdown_preview(output_path)
    append_artifact_evidence(
        run_dir,
        source_id=str(source_id),
        artifact_path=artifact_path,
        evidence_type="web_markdown",
        parser_name=result.parser_name,
        summary=summary,
        raw=result.metadata,
    )
    return True, result.output_paths


def _run_git_clone(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = str(job.get("source_id", ""))
    url = _find_source_url(run_dir, source_id)
    if not url:
        _write_parse_error(run_dir, source_id, "git_clone", "source has no repository URL")
        return False, []
    try:
        from autoad_researcher.repository_intelligence.acquisition import (
            RepositoryAcquisitionRequest,
            RepositoryAcquisitionRunner,
        )
        from autoad_researcher.repository_intelligence.discovery import parse_github_repository_url
        from autoad_researcher.tools.providers import GitHubReadProvider

        acquisition_dir = run_dir / "repo_acquisition" / source_id
        _cleanup_incomplete_repository_target(run_dir, source_id)
        try:
            locator = parse_github_repository_url(url, strict=True)
        except Exception:
            result = RepositoryAcquisitionRunner(timeout_seconds=120).acquire(
                RepositoryAcquisitionRequest(
                    schema_version=1,
                    source_id=source_id,
                    workspace_root=run_dir,
                    remote_url=url,
                    acquisition_profile="generic_shallow",
                ),
                run_dir=acquisition_dir,
            )
        else:
            metadata = GitHubReadProvider().repository_metadata(locator.owner, locator.repository)
            resolved_ref = metadata.default_branch
            commit = GitHubReadProvider().commit_ref(metadata.owner, metadata.repository, resolved_ref)
            result = RepositoryAcquisitionRunner(timeout_seconds=120).acquire(
                RepositoryAcquisitionRequest(
                    schema_version=1,
                    source_id=source_id,
                    workspace_root=run_dir,
                    remote_url=locator.canonical_url,
                    resolved_ref=resolved_ref,
                    resolved_commit=commit.sha,
                    acquisition_profile="shallow_ref",
                ),
                run_dir=acquisition_dir,
            )
        if result.status != "success":
            _write_parse_error(run_dir, source_id, "git_clone", result.error_message or "repository acquisition failed")
            return False, []
        outputs = [f"repos/{source_id}"]
        for rel in (
            "repo_acquisition/{source_id}/repository_source.json",
            "repo_acquisition/{source_id}/repository_attestation.json",
            "repo_acquisition/{source_id}/evidence_index.jsonl",
        ):
            outputs.append(rel.format(source_id=source_id))
        return True, outputs
    except Exception as exc:
        _write_parse_error(run_dir, source_id, "git_clone", str(exc))
        return False, []


def _run_local_repo_unpack(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = str(job.get("source_id", ""))
    source = _find_source(run_dir, source_id)
    stored_path = str((job.get("payload") if isinstance(job.get("payload"), dict) else {}).get("stored_path") or "")
    if not stored_path and source:
        stored_path = str(source.get("stored_path") or "")
    archive_path = run_dir / stored_path
    if not stored_path or not archive_path.is_file():
        _write_parse_error(run_dir, source_id, "local_repo_unpack", "uploaded repository archive not found")
        return False, []

    staging_dir = run_dir / "repo_unpack" / source_id
    extract_dir = staging_dir / "extracted"
    repo_dir = run_dir / "repos" / source_id
    acquisition_dir = run_dir / "repo_acquisition" / source_id
    shutil.rmtree(staging_dir, ignore_errors=True)
    shutil.rmtree(repo_dir, ignore_errors=True)
    shutil.rmtree(acquisition_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        _extract_repo_archive(archive_path, extract_dir)
        selected_root = _select_extracted_repo_root(extract_dir)
        shutil.copytree(selected_root, repo_dir, symlinks=False)

        from autoad_researcher.repository_intelligence.acquisition import (
            RepositoryAcquisitionRequest,
            RepositoryAcquisitionRunner,
        )
        from autoad_researcher.ui.sources import update_source_intake_result

        result = RepositoryAcquisitionRunner(timeout_seconds=120).acquire(
            RepositoryAcquisitionRequest(
                schema_version=1,
                source_id=source_id,
                workspace_root=run_dir,
                local_path=repo_dir,
                acquisition_profile="local",
            ),
            run_dir=acquisition_dir,
        )
        if result.status != "success":
            _write_parse_error(run_dir, source_id, "local_repo_unpack", result.error_message or "local repository acquisition failed")
            return False, []
        update_source_intake_result(
            run_dir,
            source_id,
            status="parsed",
            intake_status="ok",
            clear_intake_error=True,
        )
        return True, [
            f"repos/{source_id}",
            f"repo_acquisition/{source_id}/repository_source.json",
            f"repo_acquisition/{source_id}/repository_attestation.json",
            f"repo_acquisition/{source_id}/evidence_index.jsonl",
        ]
    except Exception as exc:
        _write_parse_error(run_dir, source_id, "local_repo_unpack", str(exc))
        return False, []


def _run_local_repo_acquire(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = str(job.get("source_id", ""))
    source = _find_source(run_dir, source_id)
    stored_path = str((job.get("payload") if isinstance(job.get("payload"), dict) else {}).get("stored_path") or "")
    if not stored_path and source:
        stored_path = str(source.get("stored_path") or "")
    local_path = run_dir / stored_path
    if not stored_path or not local_path.is_dir():
        _write_parse_error(run_dir, source_id, "local_repo_acquire", "local repository directory not found")
        return False, []

    repo_dir = run_dir / "repos" / source_id
    acquisition_dir = run_dir / "repo_acquisition" / source_id
    shutil.rmtree(repo_dir, ignore_errors=True)
    shutil.rmtree(acquisition_dir, ignore_errors=True)
    try:
        shutil.copytree(local_path, repo_dir, symlinks=False)
        return _attest_local_repo(run_dir, source_id, repo_dir, parser_name="local_repo_acquire")
    except Exception as exc:
        _write_parse_error(run_dir, source_id, "local_repo_acquire", str(exc))
        return False, []


def _attest_local_repo(run_dir: Path, source_id: str, repo_dir: Path, *, parser_name: str) -> tuple[bool, list[str]]:
    from autoad_researcher.repository_intelligence.acquisition import (
        RepositoryAcquisitionRequest,
        RepositoryAcquisitionRunner,
    )
    from autoad_researcher.ui.sources import update_source_intake_result

    acquisition_dir = run_dir / "repo_acquisition" / source_id
    result = RepositoryAcquisitionRunner(timeout_seconds=120).acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id=source_id,
            workspace_root=run_dir,
            local_path=repo_dir,
            acquisition_profile="local",
        ),
        run_dir=acquisition_dir,
    )
    if result.status != "success":
        _write_parse_error(run_dir, source_id, parser_name, result.error_message or "local repository acquisition failed")
        return False, []
    update_source_intake_result(
        run_dir,
        source_id,
        status="parsed",
        intake_status="ok",
        clear_intake_error=True,
    )
    return True, [
        f"repos/{source_id}",
        f"repo_acquisition/{source_id}/repository_source.json",
        f"repo_acquisition/{source_id}/repository_attestation.json",
        f"repo_acquisition/{source_id}/evidence_index.jsonl",
    ]


def _run_archive_unpack_classify(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = str(job.get("source_id", ""))
    source = _find_source(run_dir, source_id)
    stored_path = str((job.get("payload") if isinstance(job.get("payload"), dict) else {}).get("stored_path") or "")
    if not stored_path and source:
        stored_path = str(source.get("stored_path") or "")
    archive_path = run_dir / stored_path
    if not stored_path or not archive_path.is_file():
        _write_parse_error(run_dir, source_id, "archive_unpack_classify", "uploaded archive bundle not found")
        return False, []

    staging_dir = run_dir / "archive_unpack" / source_id
    extract_dir = staging_dir / "extracted"
    shutil.rmtree(staging_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        _extract_repo_archive(archive_path, extract_dir)
        repo_roots = _discover_repo_roots(extract_dir)
        child_records: list[dict[str, Any]] = []
        queued_jobs: list[dict[str, Any]] = []

        for index, repo_root in enumerate(repo_roots, start=1):
            child_id = f"{source_id}_child_{index:03d}"
            child_dir = run_dir / "sources" / child_id / "repository"
            shutil.rmtree(child_dir.parent, ignore_errors=True)
            shutil.copytree(repo_root, child_dir, symlinks=False)
            rel = child_dir.relative_to(run_dir).as_posix()
            label = repo_root.relative_to(extract_dir).as_posix() if repo_root != extract_dir else "repository"
            _append_child_source(
                run_dir,
                source_id=child_id,
                parent_source_id=source_id,
                kind="local_repo",
                user_label=label,
                stored_path=rel,
                bundle_path=label,
            )
            acquire_job = _queue_child_job(
                run_dir,
                source_id=child_id,
                job_type="local_repo_acquire",
                evidence_role="repo_acquired",
                payload={"stored_path": rel, "parent_source_id": source_id},
            )
            summarize_job = _queue_child_job(
                run_dir,
                source_id=child_id,
                job_type="repo_summarize",
                evidence_role="repo_acquired",
                payload={"depends_on": acquire_job.get("job_id"), "parent_source_id": source_id},
            )
            queued_jobs.extend([acquire_job, summarize_job])
            child_records.append({"source_id": child_id, "kind": "local_repo", "bundle_path": label})

        next_index = len(repo_roots) + 1
        for material in _discover_material_files(extract_dir, repo_roots):
            kind = _kind_for_material_file(material)
            if kind is None:
                continue
            child_id = f"{source_id}_child_{next_index:03d}"
            next_index += 1
            child_dir = run_dir / "sources" / child_id
            child_dir.mkdir(parents=True, exist_ok=True)
            dest = child_dir / material.name
            shutil.copyfile(material, dest)
            rel = dest.relative_to(run_dir).as_posix()
            bundle_path = material.relative_to(extract_dir).as_posix()
            _append_child_source(
                run_dir,
                source_id=child_id,
                parent_source_id=source_id,
                kind=kind,
                user_label=material.name,
                stored_path=rel,
                bundle_path=bundle_path,
            )
            child_records.append({"source_id": child_id, "kind": kind, "bundle_path": bundle_path})
            if kind == "paper_pdf":
                queued_jobs.append(_queue_child_job(
                    run_dir,
                    source_id=child_id,
                    job_type="paper_parse_mineru",
                    evidence_role="parsed_paper_evidence",
                    payload={"stored_path": rel, "parent_source_id": source_id},
                ))
            elif kind == "document":
                queued_jobs.append(_queue_child_job(
                    run_dir,
                    source_id=child_id,
                    job_type="document_markitdown",
                    evidence_role="parsed_document_evidence",
                    payload={"stored_path": rel, "parent_source_id": source_id},
                ))
            elif kind in {"markdown", "text"}:
                _append_uploaded_text_evidence(run_dir, child_id, rel, filename=material.name, kind=kind)

        manifest_path = _write_archive_manifest(
            run_dir,
            source_id=source_id,
            archive_path=stored_path,
            extract_dir=extract_dir,
            repo_roots=repo_roots,
            child_records=child_records,
            queued_jobs=queued_jobs,
        )
        _append_archive_manifest_evidence(run_dir, source_id, manifest_path, child_records)

        from autoad_researcher.ui.sources import update_source_intake_result

        update_source_intake_result(run_dir, source_id, status="parsed", intake_status="ok", clear_intake_error=True)
        return True, [manifest_path]
    except Exception as exc:
        _write_parse_error(run_dir, source_id, "archive_unpack_classify", str(exc))
        return False, []


def _extract_repo_archive(archive_path: Path, extract_dir: Path) -> None:
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                target = _safe_archive_target(extract_dir, member.filename)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        return
    if name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tar.xz", ".txz")):
        with tarfile.open(archive_path, mode="r:*") as archive:
            for member in archive.getmembers():
                target = _safe_archive_target(extract_dir, member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise ValueError(f"unsupported archive member type: {member.name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError(f"cannot extract archive member: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with extracted, target.open("wb") as dst:
                    shutil.copyfileobj(extracted, dst)
        return
    raise ValueError("unsupported repository archive format")


def _safe_archive_target(root: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts) or ":" in path.parts[0]:
        raise ValueError(f"unsafe archive member path: {member_name}")
    target = (root / Path(*path.parts)).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"archive member escapes target directory: {member_name}") from exc
    return target


def _select_extracted_repo_root(extract_dir: Path) -> Path:
    children = [
        child for child in extract_dir.iterdir()
        if child.name != "__MACOSX" and not child.name.startswith(".DS_Store")
    ]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    if not children:
        raise ValueError("repository archive is empty")
    return extract_dir


_REPO_MARKER_FILES = {
    ".git",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "environment.yml",
    "environment.yaml",
    "package.json",
    "Dockerfile",
    "Makefile",
    "CMakeLists.txt",
}
_CODE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cu",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".m",
    ".py",
    ".rs",
    ".sh",
    ".ts",
    ".tsx",
}
_MATERIAL_SUFFIX_TO_KIND = {
    ".pdf": "paper_pdf",
    ".doc": "document",
    ".docx": "document",
    ".html": "document",
    ".htm": "document",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
}
_IGNORED_ARCHIVE_DIRS = {
    "__MACOSX",
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    "dist",
    "node_modules",
    "site-packages",
    "__pycache__",
}
_REPO_TEXT_BASENAMES = {
    "readme",
    "license",
    "copying",
    "changelog",
    "contributing",
    "authors",
    "notice",
}


def _discover_repo_roots(extract_dir: Path) -> list[Path]:
    candidates: list[tuple[int, int, Path]] = []
    for directory in [extract_dir, *[p for p in extract_dir.rglob("*") if p.is_dir()]]:
        if _is_ignored_archive_path(directory, extract_dir):
            continue
        score = _repo_score(directory)
        if score >= 25:
            depth = len(directory.relative_to(extract_dir).parts)
            candidates.append((-score, depth, directory))
    selected: list[Path] = []
    for _neg_score, _depth, directory in sorted(candidates):
        if any(_is_relative_to(directory, existing) or _is_relative_to(existing, directory) for existing in selected):
            continue
        selected.append(directory)
    return selected


def _repo_score(directory: Path) -> int:
    score = 0
    for marker in _REPO_MARKER_FILES:
        if (directory / marker).exists():
            score += 100 if marker == ".git" else 30
    if any((directory / name).exists() for name in ("README.md", "README.rst", "README.txt", "README")):
        score += 5
    code_files = 0
    for path in directory.rglob("*"):
        if code_files >= 20:
            break
        if not path.is_file() or _is_ignored_archive_path(path, directory):
            continue
        if path.suffix.lower() in _CODE_SUFFIXES:
            code_files += 1
    score += min(code_files, 20)
    return score


def _discover_material_files(extract_dir: Path, repo_roots: list[Path]) -> list[Path]:
    materials: list[Path] = []
    for path in sorted(extract_dir.rglob("*")):
        if not path.is_file() or _is_ignored_archive_path(path, extract_dir):
            continue
        kind = _kind_for_material_file(path)
        if kind is None:
            continue
        containing_repo = next((root for root in repo_roots if _is_relative_to(path, root)), None)
        if containing_repo is not None and kind in {"markdown", "text", "document"} and _is_common_repo_text(path):
            continue
        if containing_repo is not None and kind in {"markdown", "text"}:
            continue
        materials.append(path)
    return materials


def _kind_for_material_file(path: Path) -> str | None:
    return _MATERIAL_SUFFIX_TO_KIND.get(path.suffix.lower())


def _is_common_repo_text(path: Path) -> bool:
    stem = path.stem.lower()
    return stem in _REPO_TEXT_BASENAMES or path.name.lower() in {"license", "readme", "notice"}


def _is_ignored_archive_path(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part in _IGNORED_ARCHIVE_DIRS or part.startswith(".DS_Store") for part in parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _append_child_source(
    run_dir: Path,
    *,
    source_id: str,
    parent_source_id: str,
    kind: str,
    user_label: str,
    stored_path: str,
    bundle_path: str,
) -> None:
    from autoad_researcher.assistant.v2.event_service import append_event
    from autoad_researcher.ui.sources import append_source_ref

    append_source_ref(
        run_dir,
        kind=kind,  # type: ignore[arg-type]
        user_label=user_label,
        stored_path=stored_path,
        status="uploaded_not_parsed",
        source_id=source_id,
        parent_source_id=parent_source_id,
        metadata={"bundle_path": bundle_path},
    )
    append_event(run_dir, "source.created", {
        "source_id": source_id,
        "kind": kind,
        "stored_path": stored_path,
        "parent_source_id": parent_source_id,
    })


def _queue_child_job(
    run_dir: Path,
    *,
    source_id: str,
    job_type: str,
    evidence_role: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from autoad_researcher.assistant.v2.event_service import append_event
    from autoad_researcher.assistant.v2.job_service import append_pipeline_job

    job = append_pipeline_job(
        run_dir,
        source_id=source_id,
        job_type=job_type,
        evidence_role=evidence_role,
        payload=payload,
    )
    append_event(run_dir, "job.queued", {"job_id": job.get("job_id", ""), "job_type": job_type, "source_id": source_id})
    return job


def _append_uploaded_text_evidence(run_dir: Path, source_id: str, stored_path: str, *, filename: str, kind: str) -> None:
    from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
    from autoad_researcher.ui.sources import update_source_intake_result

    append_artifact_evidence(
        run_dir,
        source_id=source_id,
        artifact_path=stored_path,
        evidence_type="uploaded_text",
        parser_name="archive_bundle",
        summary=_markdown_preview(run_dir / stored_path),
        raw={"filename": filename, "kind": kind},
    )
    update_source_intake_result(run_dir, source_id, status="parsed", intake_status="ok", clear_intake_error=True)


def _write_archive_manifest(
    run_dir: Path,
    *,
    source_id: str,
    archive_path: str,
    extract_dir: Path,
    repo_roots: list[Path],
    child_records: list[dict[str, Any]],
    queued_jobs: list[dict[str, Any]],
) -> str:
    manifest_dir = run_dir / "archive_unpack" / source_id
    manifest_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for path in sorted(extract_dir.rglob("*")):
        if _is_ignored_archive_path(path, extract_dir):
            continue
        rel = path.relative_to(extract_dir).as_posix()
        entries.append({
            "path": rel,
            "type": "dir" if path.is_dir() else "file",
            "size": path.stat().st_size if path.is_file() else None,
        })
    manifest = {
        "schema_version": 1,
        "source_id": source_id,
        "archive_path": archive_path,
        "entries": entries[:1000],
        "truncated": len(entries) > 1000,
        "repo_roots": [root.relative_to(extract_dir).as_posix() if root != extract_dir else "." for root in repo_roots],
        "child_sources": child_records,
        "queued_jobs": [
            {"job_id": job.get("job_id"), "source_id": job.get("source_id"), "job_type": job.get("job_type")}
            for job in queued_jobs
        ],
    }
    path = manifest_dir / "archive_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path.relative_to(run_dir).as_posix()


def _append_archive_manifest_evidence(run_dir: Path, source_id: str, manifest_path: str, child_records: list[dict[str, Any]]) -> None:
    from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence

    counts: dict[str, int] = {}
    for child in child_records:
        kind = str(child.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    summary = "资料包已解包分类：" + ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items())) if counts else "资料包已解包，但未发现可解析资料。"
    append_artifact_evidence(
        run_dir,
        source_id=source_id,
        artifact_path=manifest_path,
        evidence_type="archive_manifest",
        parser_name="archive_bundle_classifier",
        summary=summary,
        raw={"child_sources": child_records, "counts": counts},
    )


def _run_paper_parse_mineru(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = job.get("source_id", "")
    sources = _load_sources(run_dir)
    for s in sources:
        if s.get("source_id") == source_id:
            pdf_path = run_dir / (s.get("stored_path") or "")
            if pdf_path.is_file():
                import subprocess
                r = subprocess.run(
                    [sys.executable, "-m", "autoad_researcher.cli", "paper-intelligence", "--run-id", run_dir.name, "--pdf", str(pdf_path), "--json"],
                    capture_output=True, text=True, timeout=600,
                    cwd=str(run_dir.parent.parent)
                )
                if r.returncode == 0:
                    outputs = _paper_outputs(run_dir, source_id)
                    summary_ok, summary_outputs = _run_paper_summarize(run_dir, job)
                    if summary_ok:
                        outputs.extend(summary_outputs)
                    return True, _dedupe_outputs(outputs)
                _write_parse_error(run_dir, source_id, "mineru_pipeline_v1", r.stderr or r.stdout or "paper-intelligence failed")
                return _run_paper_fallbacks(run_dir, job)
            _write_parse_error(run_dir, source_id, "mineru_pipeline_v1", "source PDF file not found")
            return False, []
    return False, []


def _run_paper_parse_markitdown(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = str(job.get("source_id", ""))
    source = _find_source(run_dir, source_id)
    stored_path = str(source.get("stored_path") or "") if source else ""
    if not stored_path:
        _write_parse_error(run_dir, source_id, "markitdown", "source has no stored_path")
        return False, []
    input_path = run_dir / stored_path
    output_dir = run_dir / "paper" / "parse" / "markitdown" / source_id
    output_path = output_dir / "paper.md"

    from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
    from autoad_researcher.tools.markitdown_adapter import convert_local_to_markdown

    result = convert_local_to_markdown(input_path, output_path, run_dir=run_dir)
    if not result.ok:
        _write_parse_error(run_dir, source_id, "markitdown", result.error or "markitdown failed")
        return False, []
    artifact_path = result.output_paths[0]
    _mark_source_parsed_via_fallback(run_dir, source_id, artifact_path)
    append_artifact_evidence(
        run_dir,
        source_id=source_id,
        artifact_path=artifact_path,
        evidence_type="paper_markdown_fallback",
        parser_name=result.parser_name,
        summary=_markdown_preview(output_path),
        raw={**result.metadata, "fallback_for": "paper_parse_mineru"},
    )
    summary_ok, summary_outputs = _run_paper_summarize(run_dir, {
        **job,
        "payload": {
            **(job.get("payload") if isinstance(job.get("payload"), dict) else {}),
            "paper_markdown_path": artifact_path,
            "parser_name": result.parser_name,
        },
    })
    return True, _dedupe_outputs(result.output_paths + (summary_outputs if summary_ok else []))


def _run_document_markitdown(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = str(job.get("source_id", ""))
    source = _find_source(run_dir, source_id)
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    stored_path = str(payload.get("stored_path") or (source.get("stored_path") if source else "") or "")
    if not stored_path:
        _write_parse_error(run_dir, source_id, "document_markitdown", "source has no stored_path")
        return False, []
    input_path = run_dir / stored_path
    output_dir = run_dir / "document" / "parse" / "markitdown" / source_id
    output_path = output_dir / "document.md"

    from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
    from autoad_researcher.tools.markitdown_adapter import convert_local_to_markdown
    from autoad_researcher.ui.sources import update_source_intake_result

    result = convert_local_to_markdown(input_path, output_path, run_dir=run_dir)
    if not result.ok:
        _write_parse_error(run_dir, source_id, "document_markitdown", result.error or "markitdown failed")
        return False, []
    artifact_path = result.output_paths[0]
    update_source_intake_result(run_dir, source_id, status="parsed", intake_status="ok", clear_intake_error=True)
    append_artifact_evidence(
        run_dir,
        source_id=source_id,
        artifact_path=artifact_path,
        evidence_type="document_markdown",
        parser_name=result.parser_name,
        summary=_markdown_preview(output_path),
        raw=result.metadata,
    )
    return True, result.output_paths


def _run_paper_fallbacks(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    for parser_name, runner in (
        ("pdftotext", _run_paper_parse_pdftotext),
        ("markitdown", _run_paper_parse_markitdown),
        ("arxiv_abs", _run_paper_parse_arxiv_abs),
    ):
        ok, outputs = runner(run_dir, job)
        if ok:
            return True, outputs
        if not _has_parser_error(run_dir, str(job.get("source_id", "")), parser_name):
            _write_parse_error(run_dir, str(job.get("source_id", "")), parser_name, f"{parser_name} fallback failed")
    return False, []


def _run_paper_parse_pdftotext(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = str(job.get("source_id", ""))
    source = _find_source(run_dir, source_id)
    stored_path = str(source.get("stored_path") or "") if source else ""
    if not stored_path:
        _write_parse_error(run_dir, source_id, "pdftotext", "source has no stored_path")
        return False, []
    input_path = run_dir / stored_path
    output_dir = run_dir / "paper" / "parse" / "pdftotext" / source_id
    output_path = output_dir / "paper.md"

    from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
    from autoad_researcher.tools.pdf_text_adapter import convert_pdf_to_markdown

    result = convert_pdf_to_markdown(input_path, output_path, run_dir=run_dir)
    if not result.ok:
        _write_parse_error(run_dir, source_id, "pdftotext", result.error or "pdftotext failed")
        return False, []
    artifact_path = result.output_paths[0]
    _mark_source_parsed_via_fallback(run_dir, source_id, artifact_path)
    append_artifact_evidence(
        run_dir,
        source_id=source_id,
        artifact_path=artifact_path,
        evidence_type="paper_markdown_fallback",
        parser_name=result.parser_name,
        summary=_markdown_preview(output_path),
        raw={**result.metadata, "fallback_for": "paper_parse_mineru"},
    )
    summary_ok, summary_outputs = _run_paper_summarize(run_dir, {
        **job,
        "payload": {
            **(job.get("payload") if isinstance(job.get("payload"), dict) else {}),
            "paper_markdown_path": artifact_path,
            "parser_name": result.parser_name,
        },
    })
    return True, _dedupe_outputs(result.output_paths + (summary_outputs if summary_ok else []))


def _run_paper_parse_arxiv_abs(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = str(job.get("source_id", ""))
    source = _find_source(run_dir, source_id)
    label = " ".join(str(source.get(key) or "") for key in ("user_label", "stored_path")) if source else ""
    arxiv_id = _extract_arxiv_id(label)
    if not arxiv_id:
        _write_parse_error(run_dir, source_id, "arxiv_abs", "no arXiv id found in source label/path")
        return False, []
    url = f"https://arxiv.org/abs/{arxiv_id}"
    output_dir = run_dir / "paper" / "parse" / "arxiv_abs" / source_id
    output_path = output_dir / "paper.md"
    try:
        from autoad_researcher.tools.providers import SecureWebFetchProvider
        fetched = SecureWebFetchProvider().fetch(url)
        markdown = _arxiv_abs_html_to_markdown(fetched.content, arxiv_id, url)
    except Exception as exc:
        _write_parse_error(run_dir, source_id, "arxiv_abs", f"arXiv abs fetch failed: {exc}")
        return False, []
    if not markdown:
        _write_parse_error(run_dir, source_id, "arxiv_abs", "arXiv abs page produced no readable markdown")
        return False, []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    artifact_path = str(output_path.relative_to(run_dir))
    _mark_source_parsed_via_fallback(run_dir, source_id, artifact_path)

    from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
    append_artifact_evidence(
        run_dir,
        source_id=source_id,
        artifact_path=artifact_path,
        evidence_type="paper_markdown_fallback",
        parser_name="arxiv_abs",
        summary=_markdown_preview(output_path),
        raw={"url": url, "arxiv_id": arxiv_id, "fallback_for": "paper_parse_mineru"},
    )
    summary_ok, summary_outputs = _run_paper_summarize(run_dir, {
        **job,
        "payload": {
            **(job.get("payload") if isinstance(job.get("payload"), dict) else {}),
            "paper_markdown_path": artifact_path,
            "parser_name": "arxiv_abs",
        },
    })
    return True, _dedupe_outputs([artifact_path] + (summary_outputs if summary_ok else []))


def _run_paper_summarize(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = str(job.get("source_id", ""))
    source = _find_source(run_dir, source_id)
    if not source:
        return False, []
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    parse_attempt_id = str(
        payload.get("parse_attempt_id")
        or source.get("active_parse_attempt_id")
        or ""
    )
    paper_markdown_path = str(payload.get("paper_markdown_path") or "")
    parser_name = str(payload.get("parser_name") or "")
    if not paper_markdown_path and parse_attempt_id:
        paper_markdown_path = f"paper/parse/attempts/{parse_attempt_id}/paper.md"
    if not parser_name:
        active = _active_parse_attempt(source, parse_attempt_id)
        parser_name = str(active.get("parser") or "unknown") if active else "unknown"
    if not paper_markdown_path or not (run_dir / paper_markdown_path).is_file():
        return False, []

    from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
    from autoad_researcher.paper_intelligence.reading_artifacts import build_paper_reading_artifacts

    artifacts = build_paper_reading_artifacts(
        run_dir,
        source_id=source_id,
        parse_attempt_id=parse_attempt_id,
        paper_markdown_relpath=paper_markdown_path,
        parser_name=parser_name,
    )
    if artifacts is None:
        return False, []

    append_artifact_evidence(
        run_dir,
        source_id=source_id,
        artifact_path=artifacts.summary_md_path,
        evidence_type="paper_reading_summary",
        parser_name="paper_reading_summarizer",
        summary=artifacts.summary,
        raw={
            "parse_attempt_id": parse_attempt_id,
            "source_markdown": paper_markdown_path,
            "manifest_path": artifacts.manifest_path,
            "anchors": artifacts.anchors,
        },
    )
    append_artifact_evidence(
        run_dir,
        source_id=source_id,
        artifact_path=artifacts.manifest_path,
        evidence_type="paper_artifact_manifest",
        parser_name="paper_reading_summarizer",
        summary=f"Paper artifact manifest for {source_id}; default summary at {artifacts.summary_md_path}; detail markdown at {paper_markdown_path}",
        raw={
            "parse_attempt_id": parse_attempt_id,
            "summary_path": artifacts.summary_md_path,
            "detail_path": paper_markdown_path,
        },
    )
    return True, [
        artifacts.summary_md_path,
        artifacts.summary_json_path,
        artifacts.method_cards_path,
        artifacts.manifest_path,
    ]


def _run_repo_analyze(run_dir: Path, job: dict[str, Any]) -> tuple[bool, list[str]]:
    source_id = str(job.get("source_id", ""))
    repo_dir = run_dir / "repos" / source_id
    attestation_path = run_dir / "repo_acquisition" / source_id / "repository_attestation.json"
    if not attestation_path.is_file():
        _write_parse_error(run_dir, source_id, "repo_summarize", "repository acquisition attestation not found; clone did not complete")
        return False, []
    if not repo_dir.exists():
        _write_parse_error(run_dir, source_id, "repo_summarize", "repository directory not found")
        return False, []

    files = list(repo_dir.glob("**/*.py"))[:50]
    readme = repo_dir / "README.md"
    summary_lines = [
        f"# Repository: {source_id}",
        f"Python files: {len(files)}",
    ]
    if readme.exists():
        summary_lines.append(f"\n## README\n{readme.read_text(encoding='utf-8', errors='replace')[:2000]}")
    brief_path = repo_dir / "repo_brief.md"
    brief_path.write_text("\n".join(summary_lines))
    rel = str(brief_path.relative_to(run_dir))
    from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
    append_artifact_evidence(
        run_dir,
        source_id=source_id,
        artifact_path=rel,
        evidence_type="repo_summary",
        parser_name="repo_summarizer",
        summary=_markdown_preview(brief_path),
        raw={"python_files_sampled": len(files)},
    )
    return True, [rel]


def _cleanup_incomplete_repository_target(run_dir: Path, source_id: str) -> None:
    target = run_dir / "repos" / source_id
    attestation = run_dir / "repo_acquisition" / source_id / "repository_attestation.json"
    if attestation.is_file() or not target.exists():
        return
    shutil.rmtree(target)


def _mark_source_parsed_via_fallback(run_dir: Path, source_id: str, stored_path: str) -> None:
    try:
        from autoad_researcher.ui.sources import update_source_intake_result

        update_source_intake_result(
            run_dir,
            source_id,
            status="parsed",
            stored_path=stored_path,
            intake_status="ok",
            clear_intake_error=True,
        )
    except Exception:
        return


def _find_source_url(run_dir: Path, source_id: str) -> str:
    sources = _load_sources(run_dir)
    for s in sources:
        if s.get("source_id") == source_id:
            return s.get("user_label", "") or s.get("stored_path", "")
    return ""


def _find_source(run_dir: Path, source_id: str) -> dict[str, Any] | None:
    for source in _load_sources(run_dir):
        if source.get("source_id") == source_id:
            return source
    return None


def _load_sources(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "sources" / "source_references.json"
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("sources", [])
    except (json.JSONDecodeError, OSError):
        return []


def _paper_outputs(run_dir: Path, source_id: str) -> list[str]:
    outputs: list[str] = []
    source = _find_source(run_dir, source_id)
    active = str(source.get("active_parse_attempt_id") or "") if source else ""
    if active:
        attempt_dir = run_dir / "paper" / "parse" / "attempts" / active
        for name in ("paper.md", "parser_manifest.json", "parse_quality_report.json", "sections.json"):
            path = attempt_dir / name
            if path.is_file():
                outputs.append(str(path.relative_to(run_dir)))
    for rel in (
        "paper/artifacts/paper_summary.json",
        "paper/artifacts/paper_candidates.json",
        "paper/artifacts/method_components.json",
        "paper/evidence_index.jsonl",
    ):
        if (run_dir / rel).is_file():
            outputs.append(rel)
    return outputs or ([f"paper/parse/attempts/{active}"] if active else [])


def _active_parse_attempt(source: dict[str, Any], parse_attempt_id: str) -> dict[str, Any] | None:
    attempts = source.get("parse_attempts")
    if not isinstance(attempts, list):
        return None
    for attempt in attempts:
        if isinstance(attempt, dict) and attempt.get("parse_attempt_id") == parse_attempt_id:
            return attempt
    return None


def _dedupe_outputs(outputs: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for output in outputs:
        if output and output not in seen:
            result.append(output)
            seen.add(output)
    return result


def _write_parse_error(run_dir: Path, source_id: str, parser_name: str, error: str) -> str:
    out_dir = run_dir / "sources" / str(source_id or "unknown")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{parser_name}_error.json"
    path.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "parser_name": parser_name,
                "error": str(error)[:2000],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return str(path.relative_to(run_dir))


def _has_parser_error(run_dir: Path, source_id: str, parser_name: str) -> bool:
    return (run_dir / "sources" / str(source_id or "unknown") / f"{parser_name}_error.json").is_file()


def _best_job_error(run_dir: Path, job: dict[str, Any]) -> str:
    source_id = str(job.get("source_id", ""))
    source_dir = run_dir / "sources" / source_id
    errors: list[str] = []
    if source_dir.is_dir():
        for path in sorted(source_dir.glob("*_error.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            parser = str(payload.get("parser_name") or path.stem.removesuffix("_error"))
            error = re.sub(r"\s+", " ", str(payload.get("error") or "")).strip()
            if error:
                errors.append(f"{parser}: {error[:240]}")
    return "；".join(errors[:3]) if errors else "execution failed"


def _extract_arxiv_id(text: str) -> str | None:
    match = re.search(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)", text)
    return match.group(1) if match else None


def _arxiv_abs_html_to_markdown(html: str, arxiv_id: str, url: str) -> str:
    title = _extract_html_fragment_text(html, r"<h1[^>]*class=[\"']title[^\"']*[\"'][^>]*>(.*?)</h1>") or f"arXiv:{arxiv_id}"
    abstract = _extract_html_fragment_text(html, r"<blockquote[^>]*class=[\"']abstract[^\"']*[\"'][^>]*>(.*?)</blockquote>")
    authors = _extract_html_fragment_text(html, r"<div[^>]*class=[\"']authors[^\"']*[\"'][^>]*>(.*?)</div>")
    if not abstract:
        return ""
    title = re.sub(r"^Title:\s*", "", title).strip()
    abstract = re.sub(r"^Abstract:\s*", "", abstract).strip()
    return (
        f"# {title}\n\n"
        f"Source: {url}\n\n"
        f"## Authors\n\n{authors or 'Unknown'}\n\n"
        f"## Abstract\n\n{abstract}\n"
    )


def _extract_html_fragment_text(html: str, pattern: str) -> str:
    match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    fragment = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", match.group(1))
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?is)<[^>]+>", " ", fragment)
    from html import unescape

    return re.sub(r"\s+", " ", unescape(fragment)).strip()


def _markdown_preview(path: Path, limit: int = 1200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


if __name__ == "__main__":
    main()
