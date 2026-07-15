#!/usr/bin/env python3
"""Run and score the nine-case research-dialogue semantic acceptance suite."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.evidence_service import load_usable_evidence
from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    load_research_intent_summary,
)
from autoad_researcher.ui.chat_client import call_research_chat
from autoad_researcher.worker.main import _process_pending_jobs


DEFAULT_RUBRIC = Path("configs/benchmarks/research_semantic_cases_v1.json")
MATERIAL_CASES = {
    "case05_kernelbench",
    "case06_flashattention_feasibility",
}
MATERIAL_JOB_TYPES = {
    "git_clone",
    "repo_summarize",
    "repo_analyze",
}


class SemanticExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_operation_targets: list[str] = Field(min_length=1)
    prohibited_advisory_commitments: list[str] = Field(default_factory=list)
    required_conflict_topics: list[str] = Field(default_factory=list)
    expected_pending_confirmation: bool
    expected_execution_mode: str
    expected_source_action_types: list[str]
    veto_rules: list[str] = Field(default_factory=list)


class SemanticCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    source_url: str
    turns: list[str] = Field(min_length=1)
    expected: SemanticExpectation
    paraphrases: list[str]
    entity_variant: str
    counterfactuals: list[str]


class SemanticCaseCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    cases: list[SemanticCase] = Field(min_length=1)


class SemanticJudgeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_targets: list[str] = Field(default_factory=list)
    advisory_commitments: list[str] = Field(default_factory=list)
    conflict_topics: list[str] = Field(default_factory=list)
    execution_mode: str | None = None
    blocking_question_appropriate: bool
    veto_failures: list[str] = Field(default_factory=list)
    rationale: str = ""


class CaseRuntimeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    reply_transcript: list[dict[str, str]]
    summary: ResearchIntentSummary
    source_action_types: list[str]
    experiment_session_created: bool
    experiment_jobs_created: bool
    code_modified: bool
    evidence_checks: dict[str, bool]
    judge: SemanticJudgeObservation


def load_corpus(path: Path) -> SemanticCaseCorpus:
    corpus = SemanticCaseCorpus.model_validate_json(path.read_text(encoding="utf-8"))
    case_ids = [case.case_id for case in corpus.cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("semantic case_id values must be unique")
    if len(case_ids) != 9:
        raise ValueError(f"expected exactly nine semantic cases, got {len(case_ids)}")
    return corpus


def run_case(
    case: SemanticCase,
    *,
    run_dir: Path,
    api_key: str,
    provider_url: str,
) -> CaseRuntimeObservation:
    run_dir.mkdir(parents=True, exist_ok=False)
    transcript: list[dict[str, str]] = []
    source_result = _dialogue_turn(
        run_dir,
        case.source_url,
        transcript=transcript,
        api_key=api_key,
        provider_url=provider_url,
    )
    source_action_types = [
        "register_github_repo"
        for source in source_result.created_sources
        if source.get("kind") == "github_repo"
    ]

    if case.case_id in MATERIAL_CASES:
        _process_until_idle(run_dir)

    for turn in case.turns:
        _dialogue_turn(
            run_dir,
            turn,
            transcript=transcript,
            api_key=api_key,
            provider_url=provider_url,
        )

    if case.case_id == "case05_kernelbench":
        _process_until_idle(run_dir)

    summary = load_research_intent_summary(run_dir) or ResearchIntentSummary()
    jobs = load_pipeline_jobs(run_dir)
    evidence = load_usable_evidence(run_dir)
    evidence_checks = _evidence_checks(case.case_id, run_dir, evidence)
    judge = _judge_case(
        case,
        transcript=transcript,
        summary=summary,
        evidence=evidence,
        api_key=api_key,
        provider_url=provider_url,
    )
    if case.case_id == "case05_kernelbench":
        if not evidence_checks.get("exact_target_file_read", False):
            judge.veto_failures = _append_unique(
                judge.veto_failures,
                ["workload_identifier_lost", "benchmark_integrity_missing"],
            )
    if case.case_id == "case06_flashattention_feasibility":
        if not evidence_checks.get("repository_readme_evidenced", False):
            judge.veto_failures = _append_unique(
                judge.veto_failures,
                ["repository_conflict_ignored"],
            )

    experiment_jobs = [
        job
        for job in jobs
        if str(job.get("job_type") or "") not in MATERIAL_JOB_TYPES
    ]
    return CaseRuntimeObservation(
        case_id=case.case_id,
        reply_transcript=transcript,
        summary=summary,
        source_action_types=source_action_types,
        experiment_session_created=(run_dir / "experiments" / "sessions").exists(),
        experiment_jobs_created=bool(experiment_jobs),
        code_modified=any(
            (run_dir / relative).exists()
            for relative in ("code", "patches", "workspace/code")
        ),
        evidence_checks=evidence_checks,
        judge=judge,
    )


def score_case(
    case: SemanticCase,
    observation: CaseRuntimeObservation,
) -> dict[str, Any]:
    expected = case.expected
    expected_targets = set(expected.required_operation_targets)
    actual_targets = set(observation.judge.operation_targets)
    target_score = 30.0 * len(expected_targets & actual_targets) / len(expected_targets)
    advisory_score = 15.0 if not observation.judge.advisory_commitments else 0.0
    expected_conflicts = set(expected.required_conflict_topics)
    actual_conflicts = set(observation.judge.conflict_topics)
    conflict_score = (
        15.0 * len(expected_conflicts & actual_conflicts) / len(expected_conflicts)
        if expected_conflicts
        else 15.0
    )
    boundary_ok = not (
        observation.experiment_session_created
        or observation.experiment_jobs_created
        or observation.code_modified
    )
    observed_execution_mode = observation.judge.execution_mode or (
        "plan_only" if boundary_ok else ""
    )
    execution_score = (
        10.0
        if observed_execution_mode == expected.expected_execution_mode
        else 0.0
    )
    source_score = (
        10.0
        if set(observation.source_action_types) == set(expected.expected_source_action_types)
        else 0.0
    )
    boundary_score = 10.0 if boundary_ok else 0.0
    question_score = 10.0 if observation.judge.blocking_question_appropriate else 0.0
    vetoes = list(observation.judge.veto_failures)
    if not boundary_ok:
        vetoes = _append_unique(vetoes, ["plan_only_boundary_violated"])
    score = round(
        target_score
        + advisory_score
        + conflict_score
        + execution_score
        + source_score
        + boundary_score
        + question_score,
        2,
    )
    return {
        "case_id": case.case_id,
        "score": score,
        "passes_case_threshold": score >= 85.0 and not vetoes,
        "veto_failures": vetoes,
        "missing_operation_targets": sorted(expected_targets - actual_targets),
        "advisory_commitments": observation.judge.advisory_commitments,
        "missing_conflict_topics": sorted(expected_conflicts - actual_conflicts),
        "blocking_question_appropriate": observation.judge.blocking_question_appropriate,
        "source_action_types": observation.source_action_types,
        "evidence_checks": observation.evidence_checks,
        "boundary": {
            "experiment_session_created": observation.experiment_session_created,
            "experiment_jobs_created": observation.experiment_jobs_created,
            "code_modified": observation.code_modified,
        },
        "judge_rationale": observation.judge.rationale,
    }


def score_report(
    corpus: SemanticCaseCorpus,
    observations: list[CaseRuntimeObservation],
) -> dict[str, Any]:
    by_id = {item.case_id: item for item in observations}
    results = [score_case(case, by_id[case.case_id]) for case in corpus.cases]
    average = round(sum(item["score"] for item in results) / len(results), 2)
    veto_count = sum(len(item["veto_failures"]) for item in results)
    return {
        "schema_version": 1,
        "case_count": len(results),
        "average_score": average,
        "minimum_score": min(item["score"] for item in results),
        "veto_failure_count": veto_count,
        "release_gate_passed": (
            average >= 88.0
            and all(item["score"] >= 85.0 for item in results)
            and veto_count == 0
        ),
        "results": results,
    }


def _dialogue_turn(
    run_dir: Path,
    user_input: str,
    *,
    transcript: list[dict[str, str]],
    api_key: str,
    provider_url: str,
):
    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=user_input,
        transcript_tail=transcript[-12:],
        api_key=api_key,
        provider_url=provider_url,
    )
    transcript.extend(
        [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": result.reply},
        ]
    )
    return result


def _process_until_idle(run_dir: Path, limit: int = 10) -> None:
    for _ in range(limit):
        if _process_pending_jobs(run_dir) == 0:
            return
    raise RuntimeError(f"material jobs did not become idle for {run_dir.name}")


def _evidence_checks(
    case_id: str,
    run_dir: Path,
    evidence: list[dict[str, Any]],
) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    if case_id == "case05_kernelbench":
        target_items = [
            item
            for item in evidence
            if item.get("evidence_type") == "repository_target_evidence"
            and (item.get("raw") or {}).get("target") == {"level": 2, "problem_id": 40}
        ]
        checks["exact_target_file_read"] = any(
            isinstance(item.get("artifact_path"), str)
            and (run_dir / item["artifact_path"]).is_file()
            and int((item.get("raw") or {}).get("bytes_read") or 0) > 0
            for item in target_items
        )
    if case_id == "case06_flashattention_feasibility":
        checks["repository_readme_evidenced"] = any(
            item.get("evidence_type") == "repo_summary"
            and isinstance(item.get("artifact_path"), str)
            and (run_dir / item["artifact_path"]).is_file()
            for item in evidence
        )
    return checks


def _judge_case(
    case: SemanticCase,
    *,
    transcript: list[dict[str, str]],
    summary: ResearchIntentSummary,
    evidence: list[dict[str, Any]],
    api_key: str,
    provider_url: str,
) -> SemanticJudgeObservation:
    expected = case.expected
    system = """You are a strict release evaluator for a research dialogue assistant.
Evaluate semantic coverage from the assistant replies and final summary. Do not reward facts that appear only in user messages.

Operation target meanings:
- research_goal: intended outcome and task mode
- research_object: exact baseline, method, implementation, or system under study
- dataset: data domain, dataset, category, or split constraints
- evaluation_protocol: train/test labels, comparison, or evaluation procedure
- primary_metrics: the metrics or priority objective
- success_criteria: measurable success or truthful-reporting criterion
- execution_mode: plan_only or another explicit execution boundary
- forbidden_change_scope: components or actions that must remain unchanged
- user_target_module_hints: user-specified component to test
- preferred_method_hints: user-specified technique or implementation family
- target_platform: exact hardware/runtime/precision constraints
- workload: exact model, tensor, concurrency, task ID, or training workload
- compute_environment: available memory, offload, CUDA, or resource constraints

Return operation_targets when the assistant reply or final summary preserves the substance. A concise restatement in summary.goal or summary.confirmed_facts counts as preservation; the assistant does not need to promise implementation or leave information-gathering mode.
Return advisory_commitments only from the supplied prohibited list, and only when the assistant adopts or promises one; mentioning it to reject it is not a violation.
Return conflict_topics only from the supplied required list, and only when the assistant explicitly identifies the incompatibility and its basis.
blocking_question_appropriate is true when blocking_question is null and the next planning/material step can proceed, or when the one question is genuinely necessary before any next step. It is false for form-style or unnecessary questions.
Return veto_failures only from the supplied veto list and only for a clear semantic failure. A rejected premise is not accepted. A forbidden option repeated as a constraint is not a commitment. Do not return an empty operation_targets list when the final summary explicitly contains the requested goal, object, dataset, metrics, or constraints.
For a cross-domain negative case, a concrete fusion architecture, patch serialization, parallel encoder, loss combination, or score combination is an implementation commitment even when accompanied by a caveat or material request. Flag the applicable cross-domain veto rules unless the assistant refuses to determine the implementation and limits itself to compatibility checks.
Output one JSON object with exactly: operation_targets, advisory_commitments, conflict_topics, execution_mode, blocking_question_appropriate, veto_failures, rationale."""
    compact_evidence = [
        {
            "source_id": item.get("source_id"),
            "evidence_type": item.get("evidence_type"),
            "artifact_path": item.get("artifact_path"),
            "summary": str(item.get("summary") or "")[:3000],
            "raw": item.get("raw") or {},
        }
        for item in evidence[:12]
    ]
    payload = {
        "case_id": case.case_id,
        "assistant_transcript": transcript,
        "final_summary": summary.model_dump(mode="json"),
        "usable_evidence": compact_evidence,
        "required_operation_targets": expected.required_operation_targets,
        "prohibited_advisory_commitments": expected.prohibited_advisory_commitments,
        "required_conflict_topics": expected.required_conflict_topics,
        "expected_execution_mode": expected.expected_execution_mode,
        "candidate_veto_rules": expected.veto_rules,
    }
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ]
    observation: SemanticJudgeObservation | None = None
    errors: list[str] = []
    for attempt in range(3):
        attempt_messages = list(messages)
        if attempt:
            attempt_messages.append({
                "role": "system",
                "content": "The previous transport response was invalid. Return only the required JSON object with every field present.",
            })
        result = call_research_chat(
            api_key,
            provider_url,
            attempt_messages,
            model="deepseek-v4-flash",
            timeout_s=60,
        )
        if result.get("error"):
            errors.append(str(result["error"]))
            continue
        parsed = _parse_json_object(str(result.get("reply") or ""))
        if parsed is None:
            errors.append("invalid JSON")
            continue
        try:
            candidate = SemanticJudgeObservation.model_validate(
                _normalize_judge_collections(parsed)
            )
        except ValueError as exc:
            errors.append(f"schema validation: {exc}")
            continue
        if not _judge_observation_is_consistent(candidate, summary):
            errors.append("semantic inconsistency: non-empty summary received zero targets")
            continue
        observation = candidate
        break
    if observation is None:
        raise RuntimeError(
            f"semantic judge failed after 3 attempts for {case.case_id}: {errors[-1]}"
        )
    observation.operation_targets = [
        item
        for item in observation.operation_targets
        if item in expected.required_operation_targets
    ]
    observation.advisory_commitments = [
        item
        for item in observation.advisory_commitments
        if item in expected.prohibited_advisory_commitments
    ]
    observation.conflict_topics = [
        item
        for item in observation.conflict_topics
        if item in expected.required_conflict_topics
    ]
    observation.veto_failures = [
        item for item in observation.veto_failures if item in expected.veto_rules
    ]
    return observation


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(stripped):
            if character != "{":
                continue
            try:
                payload, _end = decoder.raw_decode(stripped[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_judge_collections(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept an explicit ``{name: assessment}`` map for list-valued fields."""

    normalized = dict(payload)
    for field in (
        "operation_targets",
        "advisory_commitments",
        "conflict_topics",
        "veto_failures",
    ):
        value = normalized.get(field)
        if isinstance(value, dict):
            normalized[field] = [
                name
                for name, assessment in value.items()
                if assessment is not False and assessment is not None
            ]
    return normalized


def _judge_observation_is_consistent(
    observation: SemanticJudgeObservation,
    summary: ResearchIntentSummary,
) -> bool:
    if observation.operation_targets:
        return True
    return not (summary.goal.strip() or summary.confirmed_facts)


def _append_unique(existing: list[str], additions: list[str]) -> list[str]:
    return list(dict.fromkeys([*existing, *additions]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument(
        "--suite-dir",
        type=Path,
        help="Resume a prior suite directory and reuse completed case observations.",
    )
    parser.add_argument(
        "--rerun-case",
        action="append",
        default=[],
        help="Case ID to rerun even when a completed observation exists; repeat as needed.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    provider_url = os.environ.get("DEEPSEEK_BASE_URL", "")
    if not api_key or not provider_url:
        raise SystemExit("DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL are required")
    corpus = load_corpus(args.rubric)
    if args.suite_dir is not None:
        suite_dir = args.suite_dir
        if not suite_dir.is_dir():
            raise SystemExit(f"suite directory not found: {suite_dir}")
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suite_dir = args.runs_root / f"semantic_acceptance_{timestamp}"
        suite_dir.mkdir(parents=True, exist_ok=False)
    completed_dir = suite_dir / "completed_observations"
    completed_dir.mkdir(exist_ok=True)
    observations: list[CaseRuntimeObservation] = []
    for case in corpus.cases:
        completed_path = completed_dir / f"{case.case_id}.json"
        legacy_completed_path = suite_dir / case.case_id / "semantic_observation.json"
        reusable_path = (
            completed_path
            if completed_path.is_file()
            else legacy_completed_path
        )
        if case.case_id not in args.rerun_case and reusable_path.is_file():
            observation = CaseRuntimeObservation.model_validate_json(
                reusable_path.read_text(encoding="utf-8")
            )
            observations.append(observation)
            partial = score_case(case, observation)
            print(
                f"[semantic] reused {case.case_id}: score={partial['score']} vetoes={len(partial['veto_failures'])}",
                flush=True,
            )
            if reusable_path != completed_path:
                completed_path.write_text(
                    reusable_path.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            continue
        print(f"[semantic] running {case.case_id}", flush=True)
        case_run_dir = _next_case_run_dir(suite_dir, case.case_id)
        observation = run_case(
            case,
            run_dir=case_run_dir,
            api_key=api_key,
            provider_url=provider_url,
        )
        observations.append(observation)
        observation_text = json.dumps(
            observation.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        (case_run_dir / "semantic_observation.json").write_text(
            observation_text,
            encoding="utf-8",
        )
        completed_path.write_text(observation_text, encoding="utf-8")
        partial = score_case(case, observation)
        print(
            f"[semantic] {case.case_id}: score={partial['score']} vetoes={len(partial['veto_failures'])}",
            flush=True,
        )
    report = score_report(corpus, observations)
    report_path = suite_dir / "semantic_acceptance_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    observations_path = suite_dir / "semantic_observations.json"
    observations_path.write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in observations],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    print(f"[semantic] report={report_path}")
    return 0 if report["release_gate_passed"] else 1


def _next_case_run_dir(suite_dir: Path, case_id: str) -> Path:
    candidate = suite_dir / case_id
    if not candidate.exists():
        return candidate
    attempt = 1
    while True:
        candidate = suite_dir / f"{case_id}_retry_{attempt:02d}"
        if not candidate.exists():
            return candidate
        attempt += 1


if __name__ == "__main__":
    raise SystemExit(main())
