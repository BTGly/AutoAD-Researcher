#!/usr/bin/env python3
"""Validate an InternalBenchmarkCase YAML config file.

Usage:
    uv run python scripts/benchmark/validate_case.py configs/benchmarks/<case>.yaml
    uv run python scripts/benchmark/validate_case.py --json configs/benchmarks/<case>.yaml
    uv run python scripts/benchmark/validate_case.py --print-sha256 configs/benchmarks/<case>.yaml
"""

import argparse
import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from autoad_researcher.benchmarks.config import (  # noqa: E402
    canonical_case_json,
    compute_case_sha256,
    load_internal_benchmark_case,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate an internal benchmark case config.")
    parser.add_argument("path", help="Path to YAML benchmark case config")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--print-sha256", action="store_true", help="Print canonical SHA256")
    args = parser.parse_args(argv)

    try:
        case = load_internal_benchmark_case(args.path)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sha256 = compute_case_sha256(case)

    if args.json:
        print(json.dumps({
            "case_id": case.case_id,
            "scope": case.scope,
            "baseline": case.baseline_name,
            "repository_commit": case.repository.commit_sha,
            "dataset": f"{case.dataset.name} / {case.dataset.category}",
            "required_metrics": [m.name for m in case.evaluation.metrics if m.required],
            "case_sha256": sha256,
            "validation": "passed",
        }, ensure_ascii=False))
        return 0

    if args.print_sha256:
        print(sha256)
        return 0

    print(f"case_id: {case.case_id}")
    print(f"scope: {case.scope}")
    print(f"baseline: {case.baseline_name}")
    print(f"repository_commit: {case.repository.commit_sha}")
    print(f"dataset: {case.dataset.name} / {case.dataset.category}")
    print(f"required_metrics: {[m.name for m in case.evaluation.metrics if m.required]}")
    print(f"case_sha256: {sha256}")
    print("validation: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
