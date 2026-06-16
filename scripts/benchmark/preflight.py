#!/usr/bin/env python3
"""Benchmark preflight CLI."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

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

    try:
        import yaml
        with open(args.case) as f:
            case_data = yaml.safe_load(f)
        from types import SimpleNamespace
        case = SimpleNamespace(
            case_id=case_data["case_id"],
            repository=SimpleNamespace(
                url=case_data["repository"]["url"],
                commit_sha=case_data["repository"]["commit_sha"],
                entrypoint_path=case_data["repository"].get("entrypoint_path"),
                config_path=case_data["repository"].get("config_path"),
                dependency_files=case_data["repository"].get("dependency_files", []),
            ),
            dataset=SimpleNamespace(
                name=case_data["dataset"]["name"],
                category=case_data["dataset"]["category"],
                root_env=case_data["dataset"]["root_env"],
            ),
            evaluation=SimpleNamespace(
                evaluator_paths=case_data["evaluation"].get("evaluator_paths", []),
                protected_paths=case_data["evaluation"].get("protected_paths", []),
            ),
        )
    except Exception as exc:
        print(f"error: cannot load case: {exc}", file=sys.stderr)
        return 2

    bundle = run_preflight(
        case=case, repo_path=Path(args.repo),
        benchmark_python=Path(args.benchmark_python),
        lockfile_path=Path(args.lockfile),
        workspace_root=Path(".").resolve(),
        attempt=args.attempt,
        environ=dict(os.environ),
    )

    out.mkdir(parents=True)
    if bundle.repository_state:
        from autoad_researcher.benchmarks.io import write_json_atomic
        write_json_atomic(out / "repository_state.json", bundle.repository_state)
    if bundle.dataset_manifest:
        from autoad_researcher.benchmarks.io import write_json_atomic
        write_json_atomic(out / "dataset_manifest.json", bundle.dataset_manifest)
    if bundle.environment_snapshot:
        from autoad_researcher.benchmarks.io import write_json_atomic
        write_json_atomic(out / "environment.json", bundle.environment_snapshot)
    from autoad_researcher.benchmarks.io import write_json_atomic
    write_json_atomic(out / "preflight_report.json", bundle.report)

    if args.json_output:
        print(bundle.report.model_dump_json(indent=2))
    else:
        for c in bundle.report.checks:
            print(f"{c.name}: {c.status}")
        print(f"preflight: {'passed' if bundle.report.passed else 'failed'}")

    return 0 if bundle.report.passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
