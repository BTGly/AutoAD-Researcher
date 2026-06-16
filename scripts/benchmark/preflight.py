#!/usr/bin/env python3
"""Benchmark preflight CLI."""
import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from autoad_researcher.benchmarks.preflight import run_preflight  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Benchmark preflight check")
    p.add_argument("--case", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--benchmark-python", required=True, dest="benchmark_python")
    p.add_argument("--lockfile", required=True)
    p.add_argument("--attempt", required=True, choices=["attempt_01", "attempt_02"])
    p.add_argument("--output-dir", required=True, dest="output_dir")
    p.add_argument("--json", action="store_true", dest="json_output")
    args = p.parse_args()

    out = Path(args.output_dir)
    if out.exists():
        print(f"error: output-dir already exists: {out}", file=sys.stderr)
        return 2

    # Load case via official schema
    try:
        from autoad_researcher.benchmarks.config import load_internal_benchmark_case
        case = load_internal_benchmark_case(Path(args.case))
    except Exception as exc:
        print(f"error: cannot load case: {exc}", file=sys.stderr)
        return 2

    workspace_root = PROJECT_ROOT / "workspace"

    try:
        bundle = run_preflight(
            case=case, repo_path=Path(args.repo),
            benchmark_python=Path(args.benchmark_python),
            lockfile_path=Path(args.lockfile),
            workspace_root=workspace_root,
            attempt=args.attempt,
            environ=dict(os.environ),
        )
    except Exception as exc:
        print(f"internal preflight error: {exc}", file=sys.stderr)
        return 4

    try:
        out.mkdir(parents=True)
        from autoad_researcher.benchmarks.io import write_json_atomic
        if bundle.repository_state:
            write_json_atomic(out / "repository_state.json", bundle.repository_state)
        if bundle.dataset_manifest:
            write_json_atomic(out / "dataset_manifest.json", bundle.dataset_manifest)
        if bundle.environment_snapshot:
            write_json_atomic(out / "environment.json", bundle.environment_snapshot)
        write_json_atomic(out / "preflight_report.json", bundle.report)
    except Exception as exc:
        print(f"internal preflight error: {exc}", file=sys.stderr)
        return 4

    if args.json_output:
        print(bundle.report.model_dump_json(indent=2))
    else:
        for c in bundle.report.checks:
            print(f"{c.name}: {c.status}")
        print(f"preflight: {'passed' if bundle.report.passed else 'failed'}")

    return 0 if bundle.report.passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
