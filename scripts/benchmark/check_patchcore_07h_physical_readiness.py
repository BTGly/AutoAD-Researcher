#!/usr/bin/env python3
"""Run the 07H physical baseline gate without allocating a GPU."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from autoad_researcher.benchmarks.config import load_internal_benchmark_case  # noqa: E402
from autoad_researcher.benchmarks.patchcore_07h_readiness import (  # noqa: E402
    PhysicalReadinessGate,
    PhysicalReadinessInputs,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--required-free-vram-mb", type=int, required=True)
    parser.add_argument("--maximum-used-vram-mb", type=int, required=True)
    parser.add_argument("--case", default="configs/benchmarks/internal_patchcore_mvtec_bottle_smoke_v1.yaml")
    parser.add_argument("--repo", default="workspace/repos/patchcore-inspection")
    parser.add_argument("--benchmark-python", default="workspace/envs/patchcore_linux_gpu/bin/python")
    parser.add_argument("--lockfile", default="configs/benchmarks/environments/patchcore_linux_gpu/requirements.lock.txt")
    parser.add_argument("--environment-spec", default="configs/benchmarks/environments/patchcore_linux_gpu/environment.yaml")
    parser.add_argument("--weight", default="workspace/cache/torch_probe/hub/checkpoints/wide_resnet50_2-95faca4d.pth")
    args = parser.parse_args()
    case = load_internal_benchmark_case(_path(args.case))
    report = PhysicalReadinessGate().check(PhysicalReadinessInputs(
        case=case, source_root=Path(args.source_root), run_dir=PROJECT_ROOT / "runs" / args.run_id,
        repository_path=_path(args.repo), benchmark_python=_path(args.benchmark_python),
        lockfile_path=_path(args.lockfile), environment_spec_path=_path(args.environment_spec),
        weight_path=_path(args.weight), required_free_vram_mb=args.required_free_vram_mb,
        maximum_used_vram_mb=args.maximum_used_vram_mb,
    ))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "ready" else 3


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
