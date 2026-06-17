"""AutoAD-Researcher command-line interface."""

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from autoad_researcher.core import PipelineController
from autoad_researcher.harness.simple_pipeline import SimplePipelineHarness
from autoad_researcher.repository_intelligence.cli_runner import run_local_repository_intelligence


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="autoad",
        description="AutoAD-Researcher command-line interface",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    smoke_parser = subparsers.add_parser(
        "smoke",
        help="Run the deterministic planning smoke pipeline",
    )
    smoke_parser.add_argument(
        "--run-id",
        required=True,
        help="Run identifier, for example run_demo",
    )
    smoke_parser.add_argument(
        "--runs-root",
        default="runs",
        help="Root directory for run artifacts",
    )
    smoke_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print machine-readable JSON",
    )

    repo_parser = subparsers.add_parser(
        "repository-intelligence",
        help="Run Repository Intelligence on a local repository fixture",
    )
    repo_parser.add_argument("--run-id", required=True, help="Run identifier")
    repo_parser.add_argument("--runs-root", default="runs", help="Root directory for run artifacts")
    repo_parser.add_argument("--local-path", required=True, help="Local repository path")
    repo_parser.add_argument("--resume", action="store_true", help="Return existing result when fingerprint matches")
    repo_parser.add_argument("--json", action="store_true", dest="json_output", help="Print machine-readable JSON")

    return parser


def _result_payload(result, run_dir: Path) -> dict:
    payload = result.model_dump(mode="json", exclude_none=True)
    payload["run_dir"] = str(run_dir)
    payload["events_path"] = str(run_dir / "events.jsonl")
    return payload


def _print_human_result(result, run_dir: Path) -> None:
    print("AutoAD smoke pipeline")
    print(f"run_id: {result.run_id}")
    print(f"status: {result.status}")

    print("stages:")
    for stage in result.stages:
        artifacts = ", ".join(stage.artifacts) or "-"
        print(f"  - {stage.stage}: {stage.status} [{artifacts}]")

    if result.status == "failed":
        print(f"failed_stage: {result.failed_stage}")
        print(f"error_type: {result.error_type}")
        print(f"error_message: {result.error_message}")

    print(f"run_dir: {run_dir}")
    print(f"events: {run_dir / 'events.jsonl'}")


def run_smoke(args: argparse.Namespace) -> int:
    """执行确定性的 planning smoke pipeline。"""
    runs_root = Path(args.runs_root)

    harness = SimplePipelineHarness(runs_root=runs_root)
    controller = PipelineController(harness=harness, runs_root=runs_root)

    try:
        result = controller.run_planning_pipeline(args.run_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    run_dir = runs_root / args.run_id

    if args.json_output:
        payload = _result_payload(result, run_dir)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human_result(result, run_dir)

    return 0 if result.status == "success" else 1


def run_repository_intelligence(args: argparse.Namespace) -> int:
    """Run Repository Intelligence CLI flow."""
    try:
        summary = run_local_repository_intelligence(
            run_id=args.run_id,
            runs_root=Path(args.runs_root),
            local_path=Path(args.local_path),
            resume=args.resume,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json_output:
        print(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print("AutoAD repository intelligence")
        print(f"run_id: {summary.run_id}")
        print(f"status: {summary.status}")
        print(f"validation_status: {summary.validation_status}")
        print(f"run_dir: {summary.run_dir}")
        print(f"message: {summary.message}")
    if summary.status == "success":
        return 0
    if summary.status == "blocked":
        return 3
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "smoke":
        return run_smoke(args)
    if args.command == "repository-intelligence":
        return run_repository_intelligence(args)

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
