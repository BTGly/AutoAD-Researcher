#!/usr/bin/env python3
"""Prepare deterministic 07H MVTec manifests and read-only projections."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from autoad_researcher.benchmarks.patchcore_07h_data import prepare_07h_data  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--train-limit", type=int, default=40)
    parser.add_argument("--split-seed", type=int, default=0)
    args = parser.parse_args()
    try:
        prepared = prepare_07h_data(
            source_root=Path(args.source_root),
            run_dir=PROJECT_ROOT / "runs" / args.run_id,
            train_limit=args.train_limit,
            split_seed=args.split_seed,
        )
    except Exception as exc:
        print(f"07H data preparation blocked: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({
        "artifact_dir": str(prepared.artifact_dir.relative_to(PROJECT_ROOT)),
        "data_dir": str(prepared.data_dir.relative_to(PROJECT_ROOT)),
        "train_manifest_sha256": prepared.train_manifest_sha256,
        "b_dev_manifest_sha256": prepared.b_dev_manifest_sha256,
        "b_test_manifest_sha256": prepared.b_test_manifest_sha256,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
