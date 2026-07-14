"""Embedded V2 worker loop for the FastAPI development/product server."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from autoad_researcher.core.control_plane import CorruptAuthoritativeStore
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.worker.main import _process_pending_jobs


WORKER_ENABLED_ENV = "AUTOAD_EMBEDDED_WORKER"
WORKER_INTERVAL_ENV = "AUTOAD_EMBEDDED_WORKER_INTERVAL"


async def embedded_worker_loop() -> None:
    """Poll queued PipelineJobs so UI-created work is consumed."""
    interval = float(os.environ.get(WORKER_INTERVAL_ENV, "2.0"))
    runs_root = Path(RUNS_ROOT)
    while True:
        if runs_root.exists():
            for run_dir in sorted(runs_root.iterdir()):
                if run_dir.is_dir() and not run_dir.name.startswith("."):
                    try:
                        await asyncio.to_thread(_process_pending_jobs, run_dir)
                    except CorruptAuthoritativeStore as exc:
                        print(
                            f"[embedded-worker] authoritative store corrupt for {run_dir.name}: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    except Exception as exc:
                        print(f"[embedded-worker] failed for {run_dir.name}: {exc}", file=sys.stderr)
                        continue
        await asyncio.sleep(interval)


def embedded_worker_enabled() -> bool:
    return os.environ.get(WORKER_ENABLED_ENV, "1").strip().lower() not in {"0", "false", "no", "off"}
