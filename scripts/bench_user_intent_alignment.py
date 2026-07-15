#!/usr/bin/env python3
"""Collect raw observations for the P0 user-intent-alignment cases.

Does NOT score — writes observations.jsonl for manual judgment.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.evidence_service import load_usable_evidence
from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    load_research_intent_summary,
    save_research_intent_summary,
)
from autoad_researcher.worker.main import _process_pending_jobs

DEFAULT_RUBRIC = Path("configs/benchmarks/user_intent_p0_cases_v1.json")
BASE_RUNS = Path("runs")


class IntentCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    category: str
    expected_mode: str
    source_url: str = ""
    turns: list[str]
    expected: dict[str, Any]
    paraphrases: list[str] = Field(default_factory=list)
    setup_note: str | None = Field(default=None, alias="_setup_note")


class IntentCaseCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    description: str = ""
    cases: list[IntentCase]


class IntentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = ""
    category: str = ""
    expected_mode: str = ""
    turn_index: int | None = None
    variant: str = "original"
    user_input: str
    assistant_reply: str
    summary: dict[str, Any] | None = None
    source_action: dict[str, str] | None = None
    experiment_task: dict[str, Any] | None = None
    dialogue_mode: str = ""
    policy_assessment: dict[str, str] = Field(default_factory=dict)
    created_sources: list[dict[str, Any]] = Field(default_factory=list)
    created_jobs: list[dict[str, Any]] = Field(default_factory=list)
    experiment_session_created: bool = False
    code_modified: bool = False
    boundary_violations: list[str] = Field(default_factory=list)
    error: str = ""


def load_corpus(path: Path) -> IntentCaseCorpus:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return IntentCaseCorpus.model_validate(raw)


def _run_dialogue(
    run_dir: Path,
    user_input: str,
    transcript: list[dict[str, str]],
    api_key: str,
    provider_url: str,
    model: str,
) -> IntentObservation:
    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=user_input,
        transcript_tail=transcript[-12:],
        api_key=api_key,
        provider_url=provider_url,
        model=model,
    )
    transcript.append({"role": "user", "content": user_input})
    transcript.append({"role": "assistant", "content": result.reply})

    summary = load_research_intent_summary(run_dir)
    jobs = load_pipeline_jobs(run_dir)
    material_types = {"git_clone", "repo_summarize", "repo_analyze", "web_fetch", "web_markitdown"}
    non_material = [j for j in jobs if j.get("job_type") not in material_types]

    boundary_violations = []
    if non_material:
        boundary_violations.append("experiment_job_created")
    if (run_dir / "experiments" / "sessions").exists():
        boundary_violations.append("experiment_session_created")
    if any((run_dir / rel).exists() for rel in ("code", "patches", "workspace/code")):
        boundary_violations.append("code_modified")

    return IntentObservation(
        user_input=user_input,
        assistant_reply=result.reply,
        summary=summary.model_dump(mode="json") if summary else None,
        source_action=result.source_action,
        experiment_task=result.experiment_task,
        dialogue_mode=result.dialogue_mode,
        policy_assessment=result.policy_assessment,
        created_sources=result.created_sources,
        created_jobs=result.created_jobs,
        experiment_session_created=(run_dir / "experiments" / "sessions").exists(),
        code_modified=any(
            (run_dir / rel).exists()
            for rel in ("code", "patches", "workspace/code")
        ),
        boundary_violations=boundary_violations,
    )


def _setup_failed_parse_source(run_dir: Path) -> str:
    """Create a source with a failed parse attempt for E01 testing."""
    source_id = f"src_{uuid.uuid4().hex[:8]}"
    source_refs_path = run_dir / "sources" / "source_references.json"
    source_refs_path.parent.mkdir(parents=True, exist_ok=True)

    source_data = {
        "source_id": source_id,
        "kind": "paper_pdf",
        "user_label": "supervised_classification_paper.pdf",
        "status": "uploaded_not_parsed",
        "parse_attempts": [
            {
                "parse_attempt_id": f"pa_{uuid.uuid4().hex[:6]}",
                "status": "failed",
                "parser": "mineru_pipeline_v1",
                "quality_report": "sources/invalid_quality.json",
            }
        ],
        "active_parse_attempt_id": None,
    }

    if source_refs_path.exists():
        registry = json.loads(source_refs_path.read_text(encoding="utf-8"))
        sources = registry.get("sources", [])
        sources.append(source_data)
        registry["sources"] = sources
    else:
        registry = {"sources": [source_data]}

    source_refs_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return source_id


def _setup_two_parsed_sources(run_dir: Path) -> list[str]:
    """
    Create two sources with successful parse attempts and summary artifacts
    for E03 testing.
    """
    source_ids: list[str] = []
    papers = [
        {"title": "PatchCore: Towards Total Recall in Industrial Anomaly Detection",
         "method": "PatchCore uses coreset-subsampled memory bank of patch features"},
        {"title": "PaDiM: A Patch Distribution Modeling Framework for Anomaly Detection",
         "method": "PaDiM models multivariate Gaussian distributions of patch embeddings"},
    ]
    sources_dir = run_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    registry: list[dict[str, Any]] = []

    for i, paper in enumerate(papers):
        source_id = f"src_paper_{i}_{uuid.uuid4().hex[:4]}"
        pa_id = f"pa_paper_{i}_{uuid.uuid4().hex[:4]}"
        source_ids.append(source_id)

        paper_dir = run_dir / "paper" / "parse" / "attempts" / pa_id
        paper_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "title": paper["title"],
            "proposed_method": paper["method"],
            "research_problem": "Industrial defect detection and localization",
        }
        (paper_dir / "paper_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False), encoding="utf-8"
        )

        (paper_dir / "parse_quality_report.json").write_text(
            json.dumps({"quality_level": "usable", "source_id": source_id, "parser": "mineru_pipeline_v1"}, ensure_ascii=False),
            encoding="utf-8",
        )

        registry.append({
            "source_id": source_id,
            "kind": "paper_pdf",
            "user_label": f"paper_{i+1}.pdf",
            "status": "parsed",
            "parse_attempts": [
                {
                    "parse_attempt_id": pa_id,
                    "status": "ok",
                    "parser": "mineru_pipeline_v1",
                    "quality_report": f"paper/parse/attempts/{pa_id}/parse_quality_report.json",
                }
            ],
            "active_parse_attempt_id": pa_id,
        })

        (sources_dir / f"paper_{source_id}").mkdir(parents=True, exist_ok=True)

    source_refs_path = sources_dir / "source_references.json"
    source_refs_path.write_text(
        json.dumps({"sources": registry}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return source_ids


def _setup_cross_task_run_a(runs_root: Path) -> Path:
    """Create run A: PatchCore on bottle (used for G06 cross-task pollution test)."""
    run_a = runs_root / f"g06_run_a_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    run_a.mkdir(parents=True, exist_ok=True)

    summary = {
        "goal": "在 MVTec AD bottle 上复现 PatchCore，不改方法，只对齐论文 AUROC",
        "confirmed_facts": [
            "baseline：PatchCore（amazon-science/patchcore-inspection）",
            "数据集：MVTec AD bottle 类别",
            "主指标：image AUROC 和 pixel AUROC",
            "不改动评估脚本和测试 mask",
        ],
        "inferred_facts": [],
        "unresolved_conflicts": [],
        "blocking_question": None,
    }
    (run_a / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    source_id = f"src_{uuid.uuid4().hex[:8]}"
    (run_a / "sources").mkdir(parents=True, exist_ok=True)
    (run_a / "sources" / "source_references.json").write_text(
        json.dumps({
            "sources": [{
                "source_id": source_id,
                "kind": "github_repo",
                "user_label": "patchcore-inspection",
                "stored_path": "https://github.com/amazon-science/patchcore-inspection",
                "status": "cloned",
            }]
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return run_a


def _setup_confirmable_goal(run_dir: Path) -> None:
    """Create the prerequisite goal state required by the G03 idempotency case."""
    save_research_intent_summary(
        run_dir,
        ResearchIntentSummary(
            goal="在 MVTec AD bottle 上复现 PatchCore，并保持正式评估协议不变",
            confirmed_facts=[
                "用户选择 PatchCore 作为 baseline",
                "用户选择 MVTec AD bottle 类别",
                "用户禁止修改正式评估脚本和测试 mask",
            ],
            blocking_question=None,
        ),
    )


def _process_until_idle(run_dir: Path, limit: int = 10) -> None:
    for _ in range(limit):
        if _process_pending_jobs(run_dir) == 0:
            return
    raise RuntimeError(f"material jobs did not become idle for {run_dir.name}")


def run_case(
    case: IntentCase,
    *,
    run_dir: Path,
    api_key: str,
    provider_url: str,
    model: str,
    all_observations: list[IntentObservation],
) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)
    transcript: list[dict[str, str]] = []
    source_url = case.source_url.strip()

    if case.case_id == "E01_paper_parse_failed":
        _setup_failed_parse_source(run_dir)
    elif case.case_id == "E03_switch_active_source":
        _setup_two_parsed_sources(run_dir)
    elif case.case_id == "G03_duplicate_confirmation":
        _setup_confirmable_goal(run_dir)

    if source_url and (
        source_url.startswith("http://") or source_url.startswith("https://")
    ):
        obs = _run_dialogue(
            run_dir, source_url, transcript, api_key, provider_url, model
        )
        obs.case_id = case.case_id
        obs.category = case.category
        obs.expected_mode = case.expected_mode
        obs.turn_index = -1
        obs.variant = "source_registration"
        all_observations.append(obs)
        _process_until_idle(run_dir)

    for idx, turn in enumerate(case.turns):
        obs = _run_dialogue(
            run_dir, turn, transcript, api_key, provider_url, model
        )
        obs.case_id = case.case_id
        obs.category = case.category
        obs.expected_mode = case.expected_mode
        obs.turn_index = idx
        obs.variant = "original"
        all_observations.append(obs)

    (run_dir / "_observations.json").write_text(
        json.dumps(
            [o.model_dump(mode="json") for o in all_observations
             if o.case_id == case.case_id],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def run_cross_task_case(
    case: IntentCase,
    runs_root: Path,
    api_key: str,
    provider_url: str,
    model: str,
    all_observations: list[IntentObservation],
) -> None:
    """G06: test that a second run on cable/PaDiM does NOT inherit bottle/PatchCore state."""
    run_a = _setup_cross_task_run_a(runs_root)

    run_b = runs_root / f"g06_run_b_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    run_b.mkdir(parents=True, exist_ok=False)

    if case.source_url.strip():
        transcript_b: list[dict[str, str]] = []
        source_obs = _run_dialogue(
            run_b, case.source_url.strip(), transcript_b, api_key, provider_url, model
        )
        source_obs.case_id = case.case_id
        source_obs.category = case.category
        source_obs.expected_mode = case.expected_mode
        source_obs.turn_index = -1
        source_obs.variant = "source_registration"
        all_observations.append(source_obs)
        _process_until_idle(run_b)

    transcript_b2: list[dict[str, str]] = []
    for idx, turn in enumerate(case.turns):
        obs = _run_dialogue(
            run_b, turn, transcript_b2, api_key, provider_url, model
        )
        obs.case_id = case.case_id
        obs.category = case.category
        obs.expected_mode = case.expected_mode
        obs.turn_index = idx
        obs.variant = "original"
        all_observations.append(obs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--runs-root", type=Path, default=BASE_RUNS)
    parser.add_argument(
        "--model",
        default=os.environ.get("AUTOAD_DIALOGUE_MODEL", "deepseek-v4-flash"),
    )
    parser.add_argument("--cases", nargs="*", default=[],
                       help="Run only these case IDs. If empty, run all.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    provider_url = os.environ.get("DEEPSEEK_BASE_URL", "")
    if not api_key or not provider_url:
        raise SystemExit("DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL are required")

    corpus = load_corpus(args.config)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suite_dir = args.runs_root / f"intent_alignment_{timestamp}"
    suite_dir.mkdir(parents=True, exist_ok=False)

    all_observations: list[IntentObservation] = []
    target_ids = set(args.cases) if args.cases else None

    for case in corpus.cases:
        if target_ids and case.case_id not in target_ids:
            continue

        print(f"\n[P0-intent] ==== {case.case_id} ({case.expected_mode}) ====", flush=True)
        case_run_dir = suite_dir / case.case_id

        if case.case_id == "G06_cross_task_pollution":
            try:
                run_cross_task_case(
                    case, suite_dir, api_key, provider_url, args.model, all_observations
                )
            except Exception as exc:
                print(f"[P0-intent] {case.case_id} ERROR: {exc}", flush=True)
                all_observations.append(IntentObservation(
                    case_id=case.case_id,
                    category=case.category,
                    expected_mode=case.expected_mode,
                    user_input="",
                    assistant_reply="",
                    error=str(exc),
                ))
            continue

        try:
            run_case(
                case,
                run_dir=case_run_dir,
                api_key=api_key,
                provider_url=provider_url,
                model=args.model,
                all_observations=all_observations,
            )
        except Exception as exc:
            print(f"[P0-intent] {case.case_id} ERROR: {exc}", flush=True)
            all_observations.append(IntentObservation(
                case_id=case.case_id,
                category=case.category,
                expected_mode=case.expected_mode,
                user_input="",
                assistant_reply="",
                error=str(exc),
            ))

    output_path = suite_dir / "observations.jsonl"
    with output_path.open("w", encoding="utf-8") as f:
        for obs in all_observations:
            f.write(json.dumps(obs.model_dump(mode="json"), ensure_ascii=False) + "\n")

    print(f"\n[P0-intent] {len(all_observations)} observations → {output_path}")
    print(f"[P0-intent] Run dirs: {suite_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
