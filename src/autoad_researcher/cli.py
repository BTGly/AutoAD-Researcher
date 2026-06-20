"""AutoAD-Researcher command-line interface."""

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from autoad_researcher.core import PipelineController
from autoad_researcher.harness.simple_pipeline import SimplePipelineHarness
from autoad_researcher.pipeline.orchestrator import Orchestrator
from autoad_researcher.schemas.stage3_acceptance import Stage3AcceptanceRequest, Stage3ProviderConfig
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

    paper_parser = subparsers.add_parser(
        "paper-intelligence",
        help="Run Paper Intelligence on a PDF",
    )
    paper_parser.add_argument("--run-id", required=True, help="Run identifier")
    paper_parser.add_argument("--pdf", required=True, help="Path to paper PDF")
    paper_parser.add_argument("--parser-profile", default="mineru_pipeline_v1", help="Parser profile ID")
    paper_parser.add_argument("--budget-profile", default="standard", help="Budget profile (short/standard/long)")
    paper_parser.add_argument("--json", action="store_true", dest="json_output", help="Print machine-readable JSON")

    context_parser = subparsers.add_parser(
        "research-context",
        help="Inspect readiness of a completed paper run (read-only)",
    )
    context_parser.add_argument("--run-id", required=True, help="Run identifier")
    context_parser.add_argument("--json", action="store_true", dest="json_output", help="Print machine-readable JSON")

    td_parser = subparsers.add_parser(
        "transfer-design",
        help="Run Stage 3.4 transfer design (idea → variants → handoff)",
    )
    td_parser.add_argument("--run-id", required=True, help="Run identifier")
    td_parser.add_argument("--runs-root", default="runs", help="Root directory for run artifacts")
    td_parser.add_argument("--selected-variant-id", action="append", default=[], metavar="VARIANT_ID",
                           help="Select a variant by ID; may be repeated")
    td_parser.add_argument("--json", action="store_true", dest="json_output", help="Print machine-readable JSON")

    ep_parser = subparsers.add_parser(
        "experiment-plan",
        help="Run Stage 3.5 experiment planner (handoff → experiment bundle)",
    )
    ep_parser.add_argument("--run-id", required=True, help="Run identifier")
    ep_parser.add_argument("--runs-root", default="runs", help="Root directory for run artifacts")
    ep_parser.add_argument("--json", action="store_true", dest="json_output", help="Print machine-readable JSON")

    stage3_parser = subparsers.add_parser(
        "stage3-acceptance",
        help="Run deterministic Step 3.10 L1/L2 acceptance orchestration",
    )
    stage3_parser.add_argument("--run-id", required=True, help="Run identifier")
    stage3_parser.add_argument("--runs-root", default="runs", help="Root directory for run artifacts")
    stage3_parser.add_argument(
        "--mode",
        choices=["l1-l2", "l3-preflight"],
        default="l1-l2",
        help="Acceptance mode; L3 is preflight-only",
    )
    stage3_parser.add_argument(
        "--require-artifact",
        action="append",
        default=[],
        metavar="STAGE:RELATIVE_PATH",
        help="Require an existing run artifact for a stage; may be repeated",
    )
    stage3_parser.add_argument("--provider-base-url", default=None, help="Provider base URL for L3 preflight")
    stage3_parser.add_argument("--provider-api-key-env", default="DEEPSEEK_API_KEY", help="Environment variable name for provider API key")
    stage3_parser.add_argument("--json", action="store_true", dest="json_output", help="Print machine-readable JSON")

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


def run_paper_intelligence(args: argparse.Namespace) -> int:
    """Run Paper Intelligence CLI flow — full end-to-end orchestration."""
    from pathlib import Path

    try:
        from autoad_researcher.paper_intelligence.orchestrator import PaperIntelligenceOrchestrator
        from autoad_researcher.paper_intelligence.models import PaperIntelligenceRequest
        from autoad_researcher.paper_intelligence.agent import budget_for_profile

        pdf_path = Path(args.pdf)
        if not pdf_path.exists():
            print(f"error: PDF not found: {args.pdf}", file=sys.stderr)
            return 2

        budget = budget_for_profile(args.budget_profile)
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
            budget=budget,
        )

        orch = PaperIntelligenceOrchestrator()
        result = orch.run(request)

        if args.json_output:
            import json as _json
            print(_json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print("AutoAD paper intelligence")
            print(f"run_id: {result['run_id']}")
            print(f"status: {result['status']}")
            print(f"claims: {result.get('claim_count', 0)} (validated: {result.get('validated_claim_count', 0)}, unsupported: {result.get('unsupported_claim_count', 0)})")
            print(f"evidence_refs: {result.get('evidence_ref_count', 0)}")
            print(f"candidates: {result.get('candidate_count', 0)}")
            print(f"repairs: {result.get('repairs_used', 0)}")
            print(f"post_validation_errors: {result.get('post_validation_errors', 0)}")
            if result.get("warnings"):
                for w in result["warnings"]:
                    print(f"  warning: {w}")

        if result["status"] == "success":
            return 0
        if result["status"] == "parse_failed":
            return 3
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 7


def run_research_context(args: argparse.Namespace) -> int:
    """Build and validate the unified research context from a completed paper run."""
    import json as _json
    from pathlib import Path

    try:
        from autoad_researcher.research_context import (
            assemble_fact_ledger,
            classify_gaps,
            compute_readiness,
            detect_conflicts,
            build_unified_context_result,
            TaskContext,
        )

        run_dir = Path("runs") / args.run_id
        paper_result_path = run_dir / "paper" / "artifacts" / "paper_reader_result.json"

        paper_facts = []
        if paper_result_path.exists():
            try:
                result = _json.loads(paper_result_path.read_text(encoding="utf-8"))
            except Exception:
                pass
            else:
                # Extract facts from paper summary if available
                summary_path = run_dir / "paper" / "artifacts" / "paper_summary.json"
                if summary_path.exists():
                    try:
                        summary = _json.loads(summary_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                    else:
                        for key in ("research_problem", "proposed_method", "core_components",
                                     "training_objective", "data_assumptions"):
                            for claim in summary.get(key, []):
                                if isinstance(claim, dict):
                                    paper_facts.append({
                                        "fact_id": claim.get("claim_id", f"f_{key}"),
                                        "subject": claim.get("subject", key),
                                        "predicate": claim.get("predicate", ""),
                                        "value": claim.get("value", ""),
                                        "status": claim.get("status", "confirmed"),
                                        "evidence_ids": claim.get("evidence_ids", []),
                                        "producer_stage": "3.2_paper_intelligence",
                                    })

        task = TaskContext(task_id=f"task_{args.run_id}", goal="research context from paper analysis")
        facts = assemble_fact_ledger(paper_facts=paper_facts)
        gaps = classify_gaps(facts, task)
        conflicts = detect_conflicts(facts)
        readiness = compute_readiness(gaps, conflicts)

        # Build unified result
        uc_result = build_unified_context_result(
            run_id=args.run_id,
            paper_status="success" if paper_facts else "not_requested",
            repository_status="not_requested",
            readiness=readiness,
            draft_path=str(run_dir / "context" / "research_context_draft.json"),
            report_path=str(run_dir / "context" / "context_readiness_report.json"),
        )

        if args.json_output:
            print(_json.dumps({
                "run_id": args.run_id,
                "fact_count": len(facts),
                "gap_count": len(gaps),
                "conflict_count": len(conflicts),
                "readiness": readiness.model_dump(mode="json"),
                "context_result": uc_result.model_dump(mode="json"),
            }, ensure_ascii=False, indent=2))
        else:
            print("AutoAD research context")
            print(f"run_id: {args.run_id}")
            print(f"facts: {len(facts)}")
            print(f"gaps: {len(gaps)}")
            print(f"conflicts: {len(conflicts)}")
            print(f"readiness: {readiness.status}")
            print(f"next_stage: {readiness.next_stage}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _parse_required_artifacts(values: list[str]) -> dict[str, list[str]]:
    required: dict[str, list[str]] = {}
    for value in values:
        if ":" not in value:
            raise ValueError("--require-artifact must use STAGE:RELATIVE_PATH")
        stage, relative_path = value.split(":", 1)
        if not stage or not relative_path:
            raise ValueError("--require-artifact must include both stage and relative path")
        required.setdefault(stage, []).append(relative_path)
    return required


def run_stage3_acceptance(args: argparse.Namespace) -> int:
    """Run Step 3.10 deterministic L1/L2 acceptance orchestration."""
    try:
        request = Stage3AcceptanceRequest(
            run_id=args.run_id,
            runs_root=args.runs_root,
            mode=args.mode,
            provider_config=Stage3ProviderConfig(
                base_url=args.provider_base_url,
                api_key_env=args.provider_api_key_env,
            ),
            required_artifact_paths=_parse_required_artifacts(args.require_artifact),
        )
        result = Orchestrator().run(request)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(result.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2))
    else:
        print("AutoAD stage3 acceptance")
        print(f"run_id: {result.run_id}")
        print(f"mode: {result.mode}")
        print(f"status: {result.status}")
        if result.failed_stage:
            print(f"failed_stage: {result.failed_stage}")
        if result.failure_reason:
            print(f"failure_reason: {result.failure_reason}")
        print(f"artifact_dir: {result.artifact_dir}")

    if result.status == "passed":
        return 0
    if result.status == "blocked":
        return 3
    return 1


def run_transfer_design(args: argparse.Namespace) -> int:
    """Run Stage 3.4 transfer design as a standalone CLI command."""
    from pathlib import Path
    runs_root = Path(args.runs_root)
    run_dir = runs_root / args.run_id
    stage_dir = run_dir / "transfer_design"
    stage_dir.mkdir(parents=True, exist_ok=True)

    from autoad_researcher.pipeline.transfer_stage import run_transfer_design_stage
    record = run_transfer_design_stage(
        run_id=args.run_id,
        run_dir=run_dir,
        stage_dir=stage_dir,
    )

    if args.json_output:
        import json as _json
        print(_json.dumps(record.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2))
    else:
        print("AutoAD transfer design")
        print(f"run_id: {args.run_id}")
        print(f"status: {record.status}")
        if record.blocked_reason:
            print(f"blocked_reason: {record.blocked_reason}")
        if record.handoff_sha256:
            print(f"handoff_sha256: {record.handoff_sha256}")

    if record.status == "passed":
        return 0
    return 3 if record.status == "blocked" else 1


def run_experiment_plan(args: argparse.Namespace) -> int:
    """Run Stage 3.5 experiment planner CLI."""
    from pathlib import Path
    runs_root = Path(args.runs_root)
    run_dir = runs_root / args.run_id
    stage_dir = run_dir / "experiment_planner"
    stage_dir.mkdir(parents=True, exist_ok=True)

    from autoad_researcher.pipeline.experiment_planning_stage import run_experiment_planning_stage
    record = run_experiment_planning_stage(
        run_id=args.run_id, run_dir=run_dir, stage_dir=stage_dir,
    )

    if args.json_output:
        import json as _json
        print(_json.dumps(record.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2))
    else:
        print("AutoAD experiment planner")
        print(f"run_id: {args.run_id}")
        print(f"status: {record.status}")
        if record.blocked_reason:
            print(f"blocked_reason: {record.blocked_reason}")
        if record.handoff_sha256:
            print(f"handoff_sha256: {record.handoff_sha256}")

    if record.status == "passed":
        return 0
    return 3 if record.status == "blocked" else 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "smoke":
        return run_smoke(args)
    if args.command == "repository-intelligence":
        return run_repository_intelligence(args)
    if args.command == "paper-intelligence":
        return run_paper_intelligence(args)
    if args.command == "research-context":
        return run_research_context(args)
    if args.command == "transfer-design":
        return run_transfer_design(args)
    if args.command == "experiment-plan":
        return run_experiment_plan(args)
    if args.command == "stage3-acceptance":
        return run_stage3_acceptance(args)

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
