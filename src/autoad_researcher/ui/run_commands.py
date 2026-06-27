"""Subprocess wrappers for pipeline stages — Phase 1: preflight only."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def run_preflight(
    run_id: str,
    provider_base_url: str,
    api_key: str,
    runs_root: str = "runs",
    mode: str = "l3-preflight",
) -> dict[str, Any]:
    """Run l3-preflight via subprocess and return parsed JSON result."""
    cmd = [
        sys.executable, "-m", "autoad_researcher",
        "stage3-acceptance",
        "--run-id", run_id,
        "--mode", mode,
        "--provider-base-url", provider_base_url,
        "--json",
    ]
    env = {
        "DEEPSEEK_API_KEY": api_key,
        "AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT": os.environ.get("AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT", ""),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            cwd=str(Path.cwd()),
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            return {
                "status": "subprocess_failed",
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
        data = json.loads(stdout)
        data["_returncode"] = result.returncode
        data["_stderr"] = stderr
        return data
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "returncode": -1, "stdout": "", "stderr": "Timed out after 300s"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
