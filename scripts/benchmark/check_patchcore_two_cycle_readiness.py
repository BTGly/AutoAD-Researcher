#!/usr/bin/env python3
"""Check all prerequisites for a real two-cycle PatchCore/MVTec rehearsal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from autoad_researcher.benchmarks.config import load_internal_benchmark_case  # noqa: E402
from autoad_researcher.benchmarks.patchcore_attempt import WEIGHT_SHA256  # noqa: E402
from autoad_researcher.benchmarks.patchcore_two_cycle import (  # noqa: E402
    PatchCoreTwoCycleInputs,
    PatchCoreTwoCycleReadinessChecker,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate real PatchCore two-cycle rehearsal prerequisites without executing training"
    )
    parser.add_argument(
        "--case",
        default="configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml",
    )
    parser.add_argument("--repo", default="workspace/repos/patchcore-inspection")
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument(
        "--benchmark-python",
        default="workspace/envs/patchcore_linux_gpu/bin/python",
    )
    parser.add_argument(
        "--lockfile",
        default="configs/benchmarks/environments/patchcore_linux_gpu/requirements.lock.txt",
    )
    parser.add_argument(
        "--weight",
        default="cache/torch_probe/hub/checkpoints/wide_resnet50_2-95faca4d.pth",
    )
    parser.add_argument("--cycle-1-patch", required=True)
    parser.add_argument("--cycle-2-patch", required=True)
    args = parser.parse_args()

    case = load_internal_benchmark_case(_project_path(args.case))
    readiness = PatchCoreTwoCycleReadinessChecker().check(
        PatchCoreTwoCycleInputs(
            repository_path=_project_path(args.repo),
            expected_repository_commit=case.repository.commit_sha,
            dataset_root=_project_path(args.dataset_root),
            required_dataset_paths=case.dataset.required_relative_paths,
            benchmark_python=_project_path(args.benchmark_python),
            lockfile_path=_project_path(args.lockfile),
            weight_path=_project_path(args.weight),
            expected_weight_sha256=WEIGHT_SHA256,
            protected_paths=case.evaluation.protected_paths,
            cycle_1_patch=_project_path(args.cycle_1_patch),
            cycle_2_patch=_project_path(args.cycle_2_patch),
        )
    )
    print(json.dumps(readiness.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if readiness.status == "ready" else 3


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
