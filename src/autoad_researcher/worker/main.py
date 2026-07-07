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
import sys
import time
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
        claimed = claim_pipeline_job(run_dir, job_id)
        if not claimed:
            continue

        try:
            success = False
            if job_type == "web_search":
                success = _run_web_search(run_dir, job)
            elif job_type == "web_fetch":
                success = _run_web_fetch(run_dir, job)
            elif job_type == "git_clone":
                success = _run_git_clone(run_dir, job)
            elif job_type == "paper_parse":
                success = _run_paper_parse(run_dir, job)
            elif job_type == "repo_analyze":
                success = _run_repo_analyze(run_dir, job)
            else:
                fail_pipeline_job(run_dir, job_id, error=f"unknown job_type: {job_type}")
                continue

            if success:
                complete_pipeline_job(run_dir, job_id, outputs=[])
            else:
                fail_pipeline_job(run_dir, job_id, error="execution failed")
        except Exception as exc:
            fail_pipeline_job(run_dir, job_id, error=str(exc)[:500])
        processed += 1

    return processed


def _run_web_search(run_dir: Path, job: dict[str, Any]) -> bool:
    from autoad_researcher.assistant.material_subagents import run_material_discovery_subagent
    run_material_discovery_subagent(run_dir, request=job)
    return True


def _run_web_fetch(run_dir: Path, job: dict[str, Any]) -> bool:
    source_id = job.get("source_id", "")
    url = _find_source_url(run_dir, source_id)
    if not url:
        return False

    from autoad_researcher.tools.providers import SecureWebFetchProvider
    provider = SecureWebFetchProvider()
    result = provider.fetch(url)
    out_dir = run_dir / "sources" / source_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw.html").write_text(result.content, encoding="utf-8")
    return True


def _run_git_clone(run_dir: Path, job: dict[str, Any]) -> bool:
    source_id = job.get("source_id", "")
    url = _find_source_url(run_dir, source_id)
    if not url:
        return False
    try:
        from autoad_researcher.repository_intelligence.acquisition import shallow_clone
        shallow_clone(url, run_dir / "repos" / source_id)
        return True
    except Exception:
        return False


def _run_paper_parse(run_dir: Path, job: dict[str, Any]) -> bool:
    source_id = job.get("source_id", "")
    sources = _load_sources(run_dir)
    for s in sources:
        if s.get("source_id") == source_id:
            pdf_path = run_dir / (s.get("stored_path") or "")
            if pdf_path.is_file():
                import subprocess
                r = subprocess.run(
                    ["uv", "run", "autoad", "paper-intelligence", "--run-id", run_dir.name, "--pdf", str(pdf_path), "--json"],
                    capture_output=True, text=True, timeout=600,
                    cwd=str(run_dir.parent.parent)
                )
                return r.returncode == 0
    return False


def _run_repo_analyze(run_dir: Path, job: dict[str, Any]) -> bool:
    source_id = job.get("source_id", "")
    repo_dir = run_dir / "repos" / source_id
    if not repo_dir.exists():
        return False

    files = list(repo_dir.glob("**/*.py"))[:50]
    readme = repo_dir / "README.md"
    summary_lines = [
        f"# Repository: {source_id}",
        f"Python files: {len(files)}",
    ]
    if readme.exists():
        summary_lines.append(f"\n## README\n{readme.read_text(encoding='utf-8', errors='replace')[:2000]}")
    (repo_dir / "repo_brief.md").write_text("\n".join(summary_lines))
    return True


def _find_source_url(run_dir: Path, source_id: str) -> str:
    sources = _load_sources(run_dir)
    for s in sources:
        if s.get("source_id") == source_id:
            return s.get("user_label", "") or s.get("stored_path", "")
    return ""


def _load_sources(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "sources" / "source_references.json"
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("sources", [])
    except (json.JSONDecodeError, OSError):
        return []


if __name__ == "__main__":
    main()
