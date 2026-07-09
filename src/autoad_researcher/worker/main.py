#!/usr/bin/env python3
"""AutoAD V2 Worker — polls pipeline_jobs.jsonl and executes them.

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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNS_ROOT = os.environ.get("AUTOAD_RUNS_ROOT", "runs")

JOBS_DIR = "jobs"
JOBS_FILE = "pipeline_jobs.jsonl"


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
            processed += _process_pending_jobs(run_dir)

        if processed:
            print(f"[worker] processed {processed} jobs")
        if args.once:
            break
        if not processed:
            print(f"[worker] no pending jobs, sleeping {args.interval}s")
        time.sleep(args.interval)

    print("[worker] done")


def _process_pending_jobs(run_dir: Path) -> int:
    path = run_dir / JOBS_DIR / JOBS_FILE
    if not path.is_file():
        return 0

    jobs = json.loads("[" + ",".join(path.read_text(encoding="utf-8").strip().replace("}\n{", "},{").splitlines()) + "]") if False else []

    processed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            job = json.loads(line)
        except json.JSONDecodeError:
            continue
        if job.get("status") not in ("queued",):
            continue

        job_id = job.get("job_id", "unknown")
        job_type = job.get("job_type", "")
        print(f"[worker] running {job_type} ({job_id}) in {run_dir.name}")

        from autoad_researcher.assistant.v2.job_service import claim_pipeline_job, complete_pipeline_job, fail_pipeline_job
        from autoad_researcher.assistant.v2.event_service import append_event

        dependency = _dependency_status(run_dir, job)
        if dependency == "pending":
            continue
        if dependency == "failed":
            claimed = claim_pipeline_job(run_dir, job_id)
            if claimed:
                error = f"dependency failed: {job.get('payload', {}).get('depends_on')}"
                fail_pipeline_job(run_dir, job_id, error=error)
                append_event(run_dir, "job.failed", {"job_id": job_id, "job_type": job_type, "source_id": job.get("source_id", ""), "error": error})
                append_event(run_dir, "toast.error", {"message": f"{job_type} 失败：{error}"})
                processed += 1
            continue

        claimed = claim_pipeline_job(run_dir, job_id)
        if not claimed:
            continue

        append_event(run_dir, "job.started", {"job_id": job_id, "job_type": job_type})
        success = False
        outputs: list[str] = []

        try:
            if job_type == "web_search":
                success = _run_web_search(run_dir, job)
            elif job_type == "web_fetch":
                success, outputs = _run_web_fetch(run_dir, job)
            elif job_type == "web_markitdown":
                success, outputs = _run_web_markitdown(run_dir, job)
            elif job_type == "git_clone":
                success, outputs = _run_git_clone(run_dir, job)
            elif job_type in {"paper_parse", "paper_parse_mineru"}:
                success, outputs = _run_paper_parse_mineru(run_dir, job)
            elif job_type == "paper_parse_markitdown":
                success, outputs = _run_paper_parse_markitdown(run_dir, job)
            elif job_type == "paper_summarize":
                success, outputs = _run_paper_summarize(run_dir, job)
            elif job_type in {"repo_analyze", "repo_summarize"}:
                success, outputs = _run_repo_analyze(run_dir, job)
            else:
                fail_pipeline_job(run_dir, job_id, error=f"unknown job_type: {job_type}")
                append_event(run_dir, "job.failed", {"job_id": job_id, "error": f"unknown job_type: {job_type}"})
                continue

            if success:
                complete_pipeline_job(run_dir, job_id, outputs=outputs)
                append_event(run_dir, "job.completed", {"job_id": job_id, "outputs": outputs})
                if outputs:
                    append_event(run_dir, "artifact.created", {"job_id": job_id, "paths": outputs})
                    append_event(run_dir, "evidence.updated", {"job_id": job_id})
                append_event(run_dir, "toast.success", {"message": f"{job_type} 完成"})
            else:
                error_msg = _best_job_error(run_dir, job)
                fail_pipeline_job(run_dir, job_id, error=error_msg)
                append_event(run_dir, "job.failed", {"job_id": job_id, "job_type": job_type, "source_id": job.get("source_id", ""), "error": error_msg})
                append_event(run_dir, "toast.error", {"message": f"{job_type} 失败：{error_msg}"})
        except Exception as exc:
            error_msg = str(exc)[:500]
            fail_pipeline_job(run_dir, job_id, error=error_msg)
            append_event(run_dir, "job.failed", {"job_id": job_id, "error": error_msg})
            append_event(run_dir, "toast.error", {"message": f"{job_type} 失败"})
        processed += 1

    return processed


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

        locator = parse_github_repository_url(url, strict=True)
        metadata = GitHubReadProvider().repository_metadata(locator.owner, locator.repository)
        resolved_ref = metadata.default_branch
        commit = GitHubReadProvider().commit_ref(metadata.owner, metadata.repository, resolved_ref)
        acquisition_dir = run_dir / "repo_acquisition" / source_id
        _cleanup_incomplete_repository_target(run_dir, source_id)
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


def _dependency_status(run_dir: Path, job: dict[str, Any]) -> str | None:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    depends_on = payload.get("depends_on")
    if not depends_on:
        return None
    from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs

    for candidate in load_pipeline_jobs(run_dir):
        if candidate.get("job_id") != depends_on:
            continue
        status = candidate.get("status")
        if status == "completed":
            return "completed"
        if status == "failed":
            return "failed"
        return "pending"
    return "failed"


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
