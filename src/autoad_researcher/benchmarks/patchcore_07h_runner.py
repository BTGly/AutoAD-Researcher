"""Worker-owned execution adapter for the frozen 07H PatchCore smoke case."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from autoad_researcher.analysis import parse_metrics
from autoad_researcher.benchmarks.config import load_internal_benchmark_case
from autoad_researcher.benchmarks.patchcore_smoke import (
    build_patchcore_smoke_command_plan,
    patchcore_smoke_metric_specs,
)
from autoad_researcher.benchmarks.hashing import sha256_file


def run(*, case_path: Path, repository: Path, dataset: Path) -> int:
    """Run PatchCore below the Attempt Worker and materialize standard metrics."""
    attempt_dir = Path(os.environ["AUTOAD_ATTEMPT_DIR"]).resolve()
    case = load_internal_benchmark_case(case_path)
    entrypoint = repository / case.repository.entrypoint_path
    if not entrypoint.is_file() or not dataset.is_dir():
        return 2
    plan = build_patchcore_smoke_command_plan(
        case=case, run_id="07h", attempt="baseline_seed_0", dataset_path=str(dataset)
    )
    command = [sys.executable, str(entrypoint), *plan.args[1:]]
    _write_json(attempt_dir / "command.json", {"command_id": plan.command_id, "argv": command})
    before = _hash_protected(repository, case.evaluation.protected_paths)
    _write_json(attempt_dir / "protected_hash_before.json", before)
    result = subprocess.run(command, cwd=attempt_dir, env=os.environ.copy(), shell=False, check=False)
    after = _hash_protected(repository, case.evaluation.protected_paths)
    _write_json(attempt_dir / "protected_hash_after.json", after)
    if result.returncode != 0:
        return result.returncode
    if before != after:
        return 74
    report = parse_metrics(attempt_dir, patchcore_smoke_metric_specs(case))
    _write_json(attempt_dir / "parsed_metrics.json", report.model_dump(mode="json", exclude_none=True))
    values = {item.metric_name: item.value for item in report.metrics if item.parse_status == "parsed"}
    if report.status != "passed" or any(value is None or not 0.0 <= value <= 1.0 for value in values.values()):
        return 75
    _write_json(attempt_dir / "metrics.json", values)
    return 0


def _hash_protected(repository: Path, paths: list[str]) -> dict[str, str]:
    return {path: sha256_file(repository / path) for path in paths}


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--dataset", required=True)
    args = parser.parse_args()
    return run(case_path=Path(args.case), repository=Path(args.repository), dataset=Path(args.dataset))


if __name__ == "__main__":
    raise SystemExit(main())
