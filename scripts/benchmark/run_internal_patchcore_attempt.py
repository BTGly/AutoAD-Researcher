#!/usr/bin/env python3
"""Run one controlled internal PatchCore benchmark attempt."""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from autoad_researcher.analysis.metrics import parse_metrics  # noqa: E402
from autoad_researcher.assets import prepare_assets, write_asset_plan  # noqa: E402
from autoad_researcher.benchmarks.config import load_internal_benchmark_case  # noqa: E402
from autoad_researcher.benchmarks.dataset import resolve_dataset_root  # noqa: E402
from autoad_researcher.benchmarks.io import write_json_atomic  # noqa: E402
from autoad_researcher.benchmarks.patchcore_attempt import (  # noqa: E402
    build_patchcore_backbone_asset_plan,
    build_patchcore_command_plan,
    patchcore_metric_specs,
)
from autoad_researcher.benchmarks.preflight import run_preflight  # noqa: E402
from autoad_researcher.benchmarks.repository import collect_repository_state  # noqa: E402
from autoad_researcher.core.run_id import run_dir_path  # noqa: E402
from autoad_researcher.runner import (  # noqa: E402
    ExperimentInputRefs,
    execute_experiment_attempt,
    experiment_command_sha256,
    run_experiment_subprocess,
)
from autoad_researcher.supervisor import validate_scientific_contract  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run internal PatchCore attempt")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True, choices=["attempt_01", "attempt_02"])
    parser.add_argument(
        "--case",
        default="configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml",
    )
    parser.add_argument("--repo", default="workspace/repos/patchcore-inspection")
    parser.add_argument(
        "--benchmark-python",
        default="workspace/envs/patchcore_linux_gpu/bin/python",
    )
    parser.add_argument(
        "--lockfile",
        default="configs/benchmarks/environments/patchcore_linux_gpu/requirements.lock.txt",
    )
    parser.add_argument(
        "--weight-source",
        default="cache/torch_probe/hub/checkpoints/wide_resnet50_2-95faca4d.pth",
    )
    parser.add_argument(
        "--dataset-root",
        default=None,
        help="Optional dataset root; also sets the case root_env for preflight.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    try:
        summary = _run(args)
    except Exception as exc:
        print(f"internal attempt error: {exc}", file=sys.stderr)
        return 4

    if args.json_output:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(f"attempt: {summary['attempt_status']}")
        print(f"metrics: {summary.get('metrics_status')}")
        print(f"validity: {summary.get('validity_status')}")
    return 0 if summary["attempt_status"] == "success" and summary.get("validity_status") == "valid" else 3


def _run(args) -> dict[str, str]:
    workspace_root = PROJECT_ROOT / "workspace"
    runs_root = PROJECT_ROOT / "runs"
    run_root = run_dir_path(runs_root, args.run_id)
    attempt_dir = run_root / args.attempt
    preflight_dir = run_root / f"preflight_{args.attempt}"

    if attempt_dir.exists():
        raise FileExistsError(f"attempt output dir already exists: {attempt_dir}")
    if preflight_dir.exists():
        raise FileExistsError(f"preflight output dir already exists: {preflight_dir}")

    case = load_internal_benchmark_case(_project_path(args.case))
    repo_path = _project_path(args.repo)
    benchmark_python = _project_path(args.benchmark_python)
    lockfile_path = _project_path(args.lockfile)
    environ = dict(os.environ)
    if args.dataset_root is not None:
        environ[case.dataset.root_env] = str(_project_path(args.dataset_root))

    bundle = run_preflight(
        case=case,
        repo_path=repo_path,
        benchmark_python=benchmark_python,
        lockfile_path=lockfile_path,
        workspace_root=workspace_root,
        attempt=args.attempt,
        environ=environ,
    )
    preflight_dir.mkdir(parents=True)
    if bundle.repository_state:
        write_json_atomic(preflight_dir / "repository_state.json", bundle.repository_state)
    if bundle.dataset_manifest:
        write_json_atomic(preflight_dir / "dataset_manifest.json", bundle.dataset_manifest)
    if bundle.environment_snapshot:
        write_json_atomic(preflight_dir / "environment.json", bundle.environment_snapshot)
    write_json_atomic(preflight_dir / "preflight_report.json", bundle.report)
    if not bundle.report.passed:
        return {
            "run_id": args.run_id,
            "attempt": args.attempt,
            "attempt_status": "preflight_failed",
            "preflight_report": _rel(preflight_dir / "preflight_report.json"),
        }

    if bundle.repository_state is None or bundle.dataset_manifest is None or bundle.environment_snapshot is None:
        raise RuntimeError("preflight passed without complete evidence")

    dataset_root = resolve_dataset_root(
        case=case,
        environ=environ,
        workspace_root=workspace_root,
    )
    asset_plan = build_patchcore_backbone_asset_plan(
        run_id=args.run_id,
        source_uri=args.weight_source,
    )
    write_asset_plan(asset_plan, run_root / "assets/asset_plan.json")
    asset_manifest = prepare_assets(
        asset_plan,
        workspace_root=workspace_root,
        run_dir=run_root,
        manifest_path=run_root / "assets/asset_manifest.json",
    )
    if any(asset.status != "prepared" and asset.required for asset in asset_manifest.assets):
        return {
            "run_id": args.run_id,
            "attempt": args.attempt,
            "attempt_status": "asset_prepare_failed",
            "asset_manifest": _rel(run_root / "assets/asset_manifest.json"),
        }

    command_plan = build_patchcore_command_plan(
        run_id=args.run_id,
        attempt=args.attempt,
        dataset_path=_relative_path(dataset_root, start=attempt_dir),
    )
    input_refs = ExperimentInputRefs(
        repository_fingerprint=bundle.repository_state.repository_fingerprint,
        environment_sha256=bundle.environment_snapshot.environment_sha256,
        dataset_manifest_sha256=bundle.dataset_manifest.manifest_sha256,
        asset_manifest_sha256=asset_manifest.manifest_sha256,
        command_sha256=experiment_command_sha256(command_plan),
    )

    repository_fingerprint_after: dict[str, str | None] = {"value": None}

    def after_repository_fingerprint() -> str:
        fingerprint = collect_repository_state(
            case=case,
            repo_path=repo_path,
            workspace_root=workspace_root,
        ).repository_fingerprint
        repository_fingerprint_after["value"] = fingerprint
        return fingerprint

    execution_result = execute_experiment_attempt(
        run_id=args.run_id,
        attempt=args.attempt,
        command_plan=command_plan,
        input_refs=input_refs,
        attempt_dir=attempt_dir,
        runner=run_experiment_subprocess,
        repository_fingerprint_after=after_repository_fingerprint,
    )
    write_json_atomic(attempt_dir / "input_refs.json", input_refs)
    write_json_atomic(attempt_dir / "command.json", command_plan)

    metrics_report = parse_metrics(attempt_dir, patchcore_metric_specs())
    write_json_atomic(attempt_dir / "metrics.json", metrics_report)
    actual_category = _extract_arg_value(command_plan.args, "-d")
    actual_baseline = "PatchCore" if "patch_core" in command_plan.args else None
    validity = validate_scientific_contract(
        execution_result=execution_result,
        input_refs=input_refs,
        metrics_report=metrics_report,
        expected_repository_fingerprint=bundle.repository_state.repository_fingerprint,
        actual_repository_fingerprint=repository_fingerprint_after["value"],
        expected_category=case.dataset.category,
        actual_category=actual_category,
        expected_baseline=case.baseline_name,
        actual_baseline=actual_baseline,
        seed_fixed=case.fixed_parameters.get("seed") == 0,
        data_path_leak_detected=None,
    )
    write_json_atomic(attempt_dir / "validity_report.json", validity)
    return {
        "run_id": args.run_id,
        "attempt": args.attempt,
        "attempt_status": execution_result.status,
        "metrics_status": metrics_report.status,
        "validity_status": validity.status,
        "attempt_dir": _rel(attempt_dir),
    }


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _relative_path(path: Path, *, start: Path) -> str:
    return os.path.relpath(path.resolve(), start=start.resolve())


def _extract_arg_value(args: list[str], flag: str) -> str | None:
    try:
        index = args.index(flag)
    except ValueError:
        return None
    value_index = index + 1
    if value_index >= len(args):
        return None
    return args[value_index]


def _rel(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
