"""AutoAD-Researcher command-line interface for material intelligence."""

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from autoad_researcher.repository_intelligence.cli_runner import run_local_repository_intelligence


def build_parser() -> argparse.ArgumentParser:
    """Build the supported, non-executing CLI surface."""
    parser = argparse.ArgumentParser(
        prog="autoad",
        description="AutoAD-Researcher command-line interface",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    repository = subparsers.add_parser(
        "repository-intelligence",
        help="Analyze a local repository without modifying it",
    )
    repository.add_argument("--run-id", required=True, help="Run identifier")
    repository.add_argument("--runs-root", default="runs", help="Root directory for run artifacts")
    repository.add_argument("--local-path", required=True, help="Local repository path")
    repository.add_argument("--resume", action="store_true", help="Return an unchanged prior result")
    repository.add_argument("--json", action="store_true", dest="json_output")

    paper = subparsers.add_parser("paper-intelligence", help="Analyze a paper PDF")
    paper.add_argument("--run-id", required=True, help="Run identifier")
    paper.add_argument("--pdf", required=True, help="Path to paper PDF")
    paper.add_argument("--parser-profile", default="mineru_pipeline_v1", help="Parser profile ID")
    paper.add_argument("--budget-profile", default="standard", help="Budget profile (short/standard/long)")
    paper.add_argument("--json", action="store_true", dest="json_output")

    context = subparsers.add_parser(
        "research-context",
        help="Inspect the readiness of a completed paper run (read-only)",
    )
    context.add_argument("--run-id", required=True, help="Run identifier")
    context.add_argument("--json", action="store_true", dest="json_output")
    return parser


def run_repository_intelligence(args: argparse.Namespace) -> int:
    """Run the repository-intelligence flow."""
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
    return 0 if summary.status == "success" else 3 if summary.status == "blocked" else 1


def run_paper_intelligence(args: argparse.Namespace) -> int:
    """Run the paper-intelligence flow without starting experiment work."""
    try:
        from autoad_researcher.paper_intelligence.agent import budget_for_profile
        from autoad_researcher.paper_intelligence.models import PaperIntelligenceRequest
        from autoad_researcher.paper_intelligence.orchestrator import PaperIntelligenceOrchestrator

        pdf_path = Path(args.pdf)
        if not pdf_path.exists():
            print(f"error: PDF not found: {args.pdf}", file=sys.stderr)
            return 2
        request = PaperIntelligenceRequest(
            schema_version=1,
            request_id=f"req_{args.run_id}",
            run_id=args.run_id,
            user_goal="Paper intelligence analysis",
            paper_pdf_path=str(pdf_path),
            parser_profile_id=args.parser_profile,
            web_context_allowed=False,
            alpha_xiv_allowed=False,
            user_confirmation_policy="never",
            budget_profile=args.budget_profile,
            budget=budget_for_profile(args.budget_profile),
        )
        result = PaperIntelligenceOrchestrator().run(request)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 7

    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print("AutoAD paper intelligence")
        print(f"run_id: {result['run_id']}")
        print(f"status: {result['status']}")
        print(f"claims: {result.get('claim_count', 0)}")
        print(f"evidence_refs: {result.get('evidence_ref_count', 0)}")
    return 0 if result["status"] == "success" else 3 if result["status"] == "parse_failed" else 1


def run_research_context(args: argparse.Namespace) -> int:
    """Report the evidence readiness recorded for a paper run."""
    run_dir = Path("runs") / args.run_id
    result_path = run_dir / "paper" / "artifacts" / "paper_reader_result.json"
    payload = {
        "run_id": args.run_id,
        "paper_result_available": result_path.is_file(),
        "run_dir": str(run_dir),
    }
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("AutoAD research context")
        print(f"run_id: {args.run_id}")
        print(f"paper_result_available: {payload['paper_result_available']}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    if args.command == "repository-intelligence":
        return run_repository_intelligence(args)
    if args.command == "paper-intelligence":
        return run_paper_intelligence(args)
    if args.command == "research-context":
        return run_research_context(args)
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
