#!/usr/bin/env python3
"""AutoAD Worker — polls for pending jobs and executes them.

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
    path = run_dir / "ui_chat" / "material_requests.jsonl"
    if not path.is_file():
        return 0

    processed = 0
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if req.get("status") not in ("queued", "pending"):
            continue

        kind = req.get("kind", "")
        job_id = req.get("request_id", "unknown")
        print(f"[worker] running {kind} ({job_id}) in {run_dir.name}")

        try:
            if kind == "web_search":
                _run_web_search(run_dir, req)
            elif kind == "material_acquisition":
                _run_web_fetch(run_dir, req)
            elif kind == "repository_discovery":
                _run_repo_discovery(run_dir, req)
        except Exception as exc:
            print(f"[worker] job {job_id} failed: {exc}")

        processed += 1

    return processed


def _run_web_search(run_dir: Path, req: dict[str, Any]) -> None:
    from autoad_researcher.assistant.material_subagents import run_material_discovery_subagent

    run_material_discovery_subagent(run_dir, request=req)


def _run_web_fetch(run_dir: Path, req: dict[str, Any]) -> None:
    from autoad_researcher.assistant.material_subagents import run_web_fetch_subagent

    run_web_fetch_subagent(run_dir, request=req)


def _run_repo_discovery(run_dir: Path, req: dict[str, Any]) -> None:
    from autoad_researcher.assistant.material_subagents import run_repository_discovery_subagent

    run_repository_discovery_subagent(run_dir, request=req)


if __name__ == "__main__":
    main()
