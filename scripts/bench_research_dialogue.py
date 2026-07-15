#!/usr/bin/env python3
"""Run and score the nine-case research-dialogue semantic acceptance suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.assistant.prompt_selector import PromptSelector
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


class SemanticBudgetExceeded(RuntimeError):
    """Raised before an LLM call that would exceed a configured run limit."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SemanticRunBudget:
    """Deterministic call-count and wall-time budget for one suite run."""

    def __init__(self, *, judge_call_limit: int, wall_time_limit_seconds: float) -> None:
        self.judge_call_limit = judge_call_limit
        self.wall_time_limit_seconds = wall_time_limit_seconds
        self.dialogue_calls = 0
        self.judge_calls = 0
        self.started_at = time.monotonic()

    def reserve_dialogue_call(self) -> None:
        self._check_wall_time()
        self.dialogue_calls += 1

    def reserve_judge_call(self) -> None:
        self._check_wall_time()
        if self.judge_call_limit and self.judge_calls >= self.judge_call_limit:
            raise SemanticBudgetExceeded("judge_call_limit_exceeded")
        self.judge_calls += 1

    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def _check_wall_time(self) -> None:
        if (
            self.wall_time_limit_seconds > 0
            and self.elapsed_seconds() >= self.wall_time_limit_seconds
        ):
            raise SemanticBudgetExceeded("wall_time_limit_exceeded")


class SemanticRunManifest(BaseModel):
    """Static fingerprint that controls whether suite observations are reusable."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    dialogue_model: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    judge_independent: bool
    provider_host: str = Field(min_length=1)
    prompt_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dialogue_temperature: float = Field(ge=0.0, le=2.0)
    judge_temperature: float = Field(ge=0.0, le=2.0)
    variant_seed: int
    variant_limit: int = Field(ge=0)
    variant_count: int = Field(ge=0)
    selected_variants: dict[str, list[str]]
    judge_call_limit: int = Field(ge=0)
    wall_time_limit_seconds: float = Field(ge=0)
    created_at: str = Field(min_length=1)


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
    paraphrases: list[str] = Field(min_length=2)
    entity_variant: str = Field(min_length=1)
    counterfactuals: list[str] = Field(min_length=1)


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


class VariantPlan(BaseModel):
    """One reproducibly selected semantic mutation and its dialogue turns."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(pattern=r"^(entity|paraphrase_[0-9]{2}|counterfactual_[0-9]{2})$")
    kind: Literal["entity", "paraphrase", "counterfactual"]
    user_input: str = Field(min_length=1)
    turns: list[str] = Field(min_length=1)


class VariantJudgeObservation(BaseModel):
    """Typed semantic comparison between a base summary and one mutation."""

    model_config = ConfigDict(extra="forbid")

    semantic_equivalent: bool | None = None
    counterfactual_applied: bool | None = None
    stale_constraints: list[str] = Field(default_factory=list)
    rationale: str = ""


class VariantResult(BaseModel):
    """Complete, independently auditable outcome for one semantic mutation."""

    model_config = ConfigDict(extra="forbid")

    label: str
    kind: Literal["entity", "paraphrase", "counterfactual"]
    user_input: str
    reply_transcript: list[dict[str, str]]
    summary: ResearchIntentSummary
    source_action_types: list[str]
    boundary_violations: list[str]
    evidence_checks: dict[str, bool]
    judge: VariantJudgeObservation | None = None
    passed: bool

    @model_validator(mode="after")
    def _validate_judgment_for_kind(self) -> "VariantResult":
        if self.judge is None:
            if self.passed:
                raise ValueError("a variant without a Judge result cannot pass")
            return self
        if self.kind == "counterfactual":
            expected_pass = (
                self.judge.counterfactual_applied is True
                and not self.judge.stale_constraints
                and not self.boundary_violations
            )
        else:
            expected_pass = (
                self.judge.semantic_equivalent is True
                and not self.boundary_violations
            )
        if self.passed != expected_pass:
            raise ValueError("variant passed flag conflicts with its typed judgment")
        return self


class DeterministicRunState(BaseModel):
    """Filesystem, job, source, and evidence facts that need no LLM judgment."""

    model_config = ConfigDict(extra="forbid")

    experiment_session_created: bool
    experiment_jobs_created: bool
    code_modified: bool
    source_action_matches: bool
    evidence_checks: dict[str, bool]
    hard_failures: list[str]


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
    deterministic_failures: list[str] = Field(default_factory=list)
    judge: SemanticJudgeObservation | None = None
    variant_results: list[VariantResult] = Field(default_factory=list)
    semantic_stability: float = Field(default=0.0, ge=0.0, le=1.0)


def load_corpus(path: Path) -> SemanticCaseCorpus:
    corpus = SemanticCaseCorpus.model_validate_json(path.read_text(encoding="utf-8"))
    case_ids = [case.case_id for case in corpus.cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("semantic case_id values must be unique")
    if len(case_ids) != 9:
        raise ValueError(f"expected exactly nine semantic cases, got {len(case_ids)}")
    return corpus


def select_variant_matrix(
    corpus: SemanticCaseCorpus,
    *,
    seed: int,
    variant_limit: int,
) -> dict[str, list[VariantPlan]]:
    """Select two paraphrases and one entity/counterfactual per Case reproducibly."""

    plans_by_case = {
        case.case_id: _case_variant_plans(case, seed=seed)
        for case in corpus.cases
    }
    all_entries = [
        (case.case_id, plan)
        for case in corpus.cases
        for plan in plans_by_case[case.case_id]
    ]
    if variant_limit <= 0 or variant_limit >= len(all_entries):
        return plans_by_case
    rng = random.Random(_stable_seed(seed, "variant_limit"))
    selected_positions = set(rng.sample(range(len(all_entries)), variant_limit))
    limited = {case.case_id: [] for case in corpus.cases}
    for position, (case_id, plan) in enumerate(all_entries):
        if position in selected_positions:
            limited[case_id].append(plan)
    return limited


def _case_variant_plans(case: SemanticCase, *, seed: int) -> list[VariantPlan]:
    rng = random.Random(_stable_seed(seed, case.case_id))
    paraphrase_indexes = sorted(rng.sample(range(len(case.paraphrases)), 2))
    counterfactual_index = rng.randrange(len(case.counterfactuals))
    plans = [
        VariantPlan(
            label="entity",
            kind="entity",
            user_input=case.entity_variant,
            turns=[case.entity_variant],
        )
    ]
    plans.extend(
        VariantPlan(
            label=f"paraphrase_{index + 1:02d}",
            kind="paraphrase",
            user_input=case.paraphrases[index],
            turns=[*case.turns, case.paraphrases[index]],
        )
        for index in paraphrase_indexes
    )
    plans.append(
        VariantPlan(
            label=f"counterfactual_{counterfactual_index + 1:02d}",
            kind="counterfactual",
            user_input=case.counterfactuals[counterfactual_index],
            turns=[*case.turns, case.counterfactuals[counterfactual_index]],
        )
    )
    return plans


def _stable_seed(seed: int, label: str) -> int:
    digest = hashlib.sha256(f"{seed}\0{label}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def run_case(
    case: SemanticCase,
    *,
    run_dir: Path,
    api_key: str,
    provider_url: str,
    model: str,
    judge_model: str,
    dialogue_temperature: float = 0.0,
    judge_temperature: float = 0.0,
    budget: SemanticRunBudget | None = None,
    variant_plans: list[VariantPlan] | None = None,
) -> CaseRuntimeObservation:
    run_dir.mkdir(parents=True, exist_ok=False)
    transcript: list[dict[str, str]] = []
    source_result = _dialogue_turn(
        run_dir,
        case.source_url,
        transcript=transcript,
        api_key=api_key,
        provider_url=provider_url,
        model=model,
        temperature=dialogue_temperature,
        budget=budget,
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
            model=model,
            temperature=dialogue_temperature,
            budget=budget,
        )

    if case.case_id == "case05_kernelbench":
        _process_until_idle(run_dir)

    summary = load_research_intent_summary(run_dir) or ResearchIntentSummary()
    evidence = load_usable_evidence(run_dir)
    state = _collect_deterministic_state(
        case,
        run_dir=run_dir,
        source_action_types=source_action_types,
        evidence=evidence,
    )
    judge = None
    if not state.hard_failures:
        judge = _judge_case(
            case,
            transcript=transcript,
            summary=summary,
            evidence=evidence,
            api_key=api_key,
            provider_url=provider_url,
            model=judge_model,
            temperature=judge_temperature,
            budget=budget,
        )
    variant_results = [
        _run_variant(
            case,
            plan=plan,
            run_dir=run_dir / "semantic_variants" / plan.label,
            base_summary=summary,
            api_key=api_key,
            provider_url=provider_url,
            model=model,
            judge_model=judge_model,
            dialogue_temperature=dialogue_temperature,
            judge_temperature=judge_temperature,
            budget=budget,
        )
        for plan in (variant_plans or [])
    ]
    semantic_stability = (
        sum(result.passed for result in variant_results) / len(variant_results)
        if variant_results
        else 0.0
    )
    return CaseRuntimeObservation(
        case_id=case.case_id,
        reply_transcript=transcript,
        summary=summary,
        source_action_types=source_action_types,
        experiment_session_created=state.experiment_session_created,
        experiment_jobs_created=state.experiment_jobs_created,
        code_modified=state.code_modified,
        evidence_checks=state.evidence_checks,
        deterministic_failures=state.hard_failures,
        judge=judge,
        variant_results=variant_results,
        semantic_stability=semantic_stability,
    )


def score_case(
    case: SemanticCase,
    observation: CaseRuntimeObservation,
) -> dict[str, Any]:
    expected = case.expected
    judge = observation.judge
    expected_targets = set(expected.required_operation_targets)
    actual_targets = set(judge.operation_targets if judge is not None else [])
    target_score = 30.0 * len(expected_targets & actual_targets) / len(expected_targets)
    advisory_commitments = judge.advisory_commitments if judge is not None else []
    advisory_score = 15.0 if judge is not None and not advisory_commitments else 0.0
    expected_conflicts = set(expected.required_conflict_topics)
    actual_conflicts = set(judge.conflict_topics if judge is not None else [])
    conflict_score = (
        15.0 * len(expected_conflicts & actual_conflicts) / len(expected_conflicts)
        if expected_conflicts and judge is not None
        else (15.0 if judge is not None else 0.0)
    )
    boundary_ok = not (
        observation.experiment_session_created
        or observation.experiment_jobs_created
        or observation.code_modified
    )
    observed_execution_mode = (
        (judge.execution_mode or ("plan_only" if boundary_ok else ""))
        if judge is not None
        else ""
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
    question_score = (
        10.0 if judge is not None and judge.blocking_question_appropriate else 0.0
    )
    vetoes = [
        *(judge.veto_failures if judge is not None else []),
        *observation.deterministic_failures,
    ]
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
        "passes_case_threshold": (
            score >= 85.0
            and not vetoes
            and bool(observation.variant_results)
            and observation.semantic_stability == 1.0
        ),
        "veto_failures": list(dict.fromkeys(vetoes)),
        "missing_operation_targets": sorted(expected_targets - actual_targets),
        "advisory_commitments": advisory_commitments,
        "missing_conflict_topics": sorted(expected_conflicts - actual_conflicts),
        "blocking_question_appropriate": (
            judge.blocking_question_appropriate if judge is not None else False
        ),
        "source_action_types": observation.source_action_types,
        "evidence_checks": observation.evidence_checks,
        "boundary": {
            "experiment_session_created": observation.experiment_session_created,
            "experiment_jobs_created": observation.experiment_jobs_created,
            "code_modified": observation.code_modified,
        },
        "deterministic_failures": observation.deterministic_failures,
        "semantic_stability": observation.semantic_stability,
        "variant_results": [
            {
                "label": result.label,
                "kind": result.kind,
                "passed": result.passed,
                "boundary_violations": result.boundary_violations,
                "rationale": result.judge.rationale if result.judge is not None else "",
            }
            for result in observation.variant_results
        ],
        "judge_rationale": judge.rationale if judge is not None else "",
    }


def score_report(
    corpus: SemanticCaseCorpus,
    observations: list[CaseRuntimeObservation],
    *,
    judge_independent: bool = True,
) -> dict[str, Any]:
    by_id = {item.case_id: item for item in observations}
    results = [score_case(case, by_id[case.case_id]) for case in corpus.cases]
    average = round(sum(item["score"] for item in results) / len(results), 2)
    veto_count = sum(len(item["veto_failures"]) for item in results)
    variant_count = sum(len(item.variant_results) for item in observations)
    variant_failure_count = sum(
        not result.passed
        for observation in observations
        for result in observation.variant_results
    )
    variant_coverage_complete = variant_count == len(corpus.cases) * 4
    return {
        "schema_version": 1,
        "case_count": len(results),
        "average_score": average,
        "minimum_score": min(item["score"] for item in results),
        "veto_failure_count": veto_count,
        "variant_count": variant_count,
        "variant_failure_count": variant_failure_count,
        "variant_coverage_complete": variant_coverage_complete,
        "judge_independent": judge_independent,
        "release_gate_passed": (
            average >= 88.0
            and all(item["score"] >= 85.0 for item in results)
            and veto_count == 0
            and variant_failure_count == 0
            and variant_coverage_complete
            and judge_independent
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
    model: str,
    temperature: float = 0.0,
    budget: SemanticRunBudget | None = None,
):
    if budget is not None:
        budget.reserve_dialogue_call()
    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=user_input,
        transcript_tail=transcript[-12:],
        api_key=api_key,
        provider_url=provider_url,
        model=model,
        temperature=temperature,
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


def _run_variant(
    case: SemanticCase,
    *,
    plan: VariantPlan,
    run_dir: Path,
    base_summary: ResearchIntentSummary,
    api_key: str,
    provider_url: str,
    model: str,
    judge_model: str,
    dialogue_temperature: float,
    judge_temperature: float,
    budget: SemanticRunBudget | None,
) -> VariantResult:
    run_dir.mkdir(parents=True, exist_ok=False)
    transcript: list[dict[str, str]] = []
    source_result = _dialogue_turn(
        run_dir,
        case.source_url,
        transcript=transcript,
        api_key=api_key,
        provider_url=provider_url,
        model=model,
        temperature=dialogue_temperature,
        budget=budget,
    )
    source_action_types = [
        "register_github_repo"
        for source in source_result.created_sources
        if source.get("kind") == "github_repo"
    ]
    if case.case_id in MATERIAL_CASES:
        _process_until_idle(run_dir)
    for turn in plan.turns:
        _dialogue_turn(
            run_dir,
            turn,
            transcript=transcript,
            api_key=api_key,
            provider_url=provider_url,
            model=model,
            temperature=dialogue_temperature,
            budget=budget,
        )
    if case.case_id == "case05_kernelbench":
        _process_until_idle(run_dir)

    summary = load_research_intent_summary(run_dir) or ResearchIntentSummary()
    evidence = load_usable_evidence(run_dir)
    state = _collect_deterministic_state(
        case,
        run_dir=run_dir,
        source_action_types=source_action_types,
        evidence=evidence,
        enforce_case_evidence=plan.kind != "entity",
    )
    violations = list(state.hard_failures)
    if not state.source_action_matches:
        violations.append("source_action_mismatch")
    judge = None
    if not state.hard_failures:
        judge = _judge_variant(
            case,
            plan=plan,
            base_summary=base_summary,
            variant_summary=summary,
            transcript=transcript,
            evidence=evidence,
            api_key=api_key,
            provider_url=provider_url,
            model=judge_model,
            temperature=judge_temperature,
            budget=budget,
        )
    if judge is None:
        passed = False
    elif plan.kind == "counterfactual":
        passed = (
            judge.counterfactual_applied is True
            and not judge.stale_constraints
            and not violations
        )
    else:
        passed = judge.semantic_equivalent is True and not violations
    return VariantResult(
        label=plan.label,
        kind=plan.kind,
        user_input=plan.user_input,
        reply_transcript=transcript,
        summary=summary,
        source_action_types=source_action_types,
        boundary_violations=violations,
        evidence_checks=state.evidence_checks,
        judge=judge,
        passed=passed,
    )


def _collect_deterministic_state(
    case: SemanticCase,
    *,
    run_dir: Path,
    source_action_types: list[str],
    evidence: list[dict[str, Any]],
    enforce_case_evidence: bool = True,
) -> DeterministicRunState:
    jobs = load_pipeline_jobs(run_dir)
    experiment_jobs = [
        job
        for job in jobs
        if str(job.get("job_type") or "") not in MATERIAL_JOB_TYPES
    ]
    experiment_session_created = (run_dir / "experiments" / "sessions").exists()
    code_modified = any(
        (run_dir / relative).exists()
        for relative in ("code", "patches", "workspace/code")
    )
    hard_failures: list[str] = []
    if experiment_session_created or experiment_jobs or code_modified:
        hard_failures.append("plan_only_boundary_violated")
    evidence_checks = (
        _evidence_checks(case.case_id, run_dir, evidence)
        if enforce_case_evidence
        else {}
    )
    if (
        case.case_id == "case05_kernelbench"
        and not evidence_checks.get("exact_target_file_read", False)
    ):
        hard_failures.extend(["workload_identifier_lost", "benchmark_integrity_missing"])
    if (
        case.case_id == "case06_flashattention_feasibility"
        and not evidence_checks.get("repository_readme_evidenced", False)
    ):
        hard_failures.append("repository_conflict_ignored")
    return DeterministicRunState(
        experiment_session_created=experiment_session_created,
        experiment_jobs_created=bool(experiment_jobs),
        code_modified=code_modified,
        source_action_matches=(
            set(source_action_types) == set(case.expected.expected_source_action_types)
        ),
        evidence_checks=evidence_checks,
        hard_failures=list(dict.fromkeys(hard_failures)),
    )


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
    model: str,
    temperature: float = 0.0,
    budget: SemanticRunBudget | None = None,
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
Output one JSON object with exactly: operation_targets, advisory_commitments, conflict_topics, execution_mode, blocking_question_appropriate, veto_failures, rationale."""
    compact_evidence = [
        {
            "source_id": item.get("source_id"),
            "evidence_type": item.get("evidence_type"),
            "artifact_path": item.get("artifact_path"),
            "summary": str(item.get("summary") or "")[:3000],
            "metadata": {
                key: (item.get("raw") or {}).get(key)
                for key in (
                    "analysis_id",
                    "repository_commit",
                    "compatibility_status",
                    "quality_level",
                    "target",
                    "bytes_read",
                    "warnings",
                    "fatal_errors",
                )
                if (item.get("raw") or {}).get(key) is not None
            },
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
        if budget is not None:
            budget.reserve_judge_call()
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
            model=model,
            timeout_s=60,
            max_tokens=4096,
            temperature=temperature,
            response_format_json=True,
        )
        if result.get("error"):
            errors.append(str(result["error"]))
            continue
        raw_reply = str(result.get("reply") or "")
        parsed = _parse_json_object(raw_reply)
        if parsed is None:
            errors.append(f"invalid JSON ({_response_shape(raw_reply)})")
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


def _judge_variant(
    case: SemanticCase,
    *,
    plan: VariantPlan,
    base_summary: ResearchIntentSummary,
    variant_summary: ResearchIntentSummary,
    transcript: list[dict[str, str]],
    evidence: list[dict[str, Any]],
    api_key: str,
    provider_url: str,
    model: str,
    temperature: float,
    budget: SemanticRunBudget | None,
) -> VariantJudgeObservation:
    system = """You are a strict semantic-mutation response evaluator.
Judge whether the final variant summary faithfully incorporates what the mutation explicitly says. Judge meaning, not wording, and never require facts or constraints absent from the mutation scenario.

For entity: compare the anonymized mutation text and variant transcript with the variant summary itself. Do not compare it to base-specific entities, datasets, metrics, or constraints. semantic_equivalent means all explicitly expressed entity-mutation intent and constraints are preserved without contradiction; a necessary clarification about placeholders is allowed.
For paraphrase: the transcript contains the original turns followed by a new paraphrase. semantic_equivalent means the new summary incorporates that paraphrase while preserving prior facts that it does not correct. A changed stance, compatibility warning, or blocking question required by the new constraint is expected behavior, not semantic drift.
For counterfactual: counterfactual_applied is true only when the explicit correction is reflected in the new summary. List in stale_constraints any superseded base constraint that incorrectly remains active. Unrelated facts may remain.

Do not infer correctness from shared keywords or entity spelling. Use the mutation text, complete transcript, and final summary semantics.
Output one JSON object with exactly: semantic_equivalent, counterfactual_applied, stale_constraints, rationale."""
    compact_evidence = [
        {
            "source_id": item.get("source_id"),
            "evidence_type": item.get("evidence_type"),
            "artifact_path": item.get("artifact_path"),
            "summary": str(item.get("summary") or "")[:1200],
        }
        for item in evidence[:6]
    ]
    payload = {
        "case_id": case.case_id,
        "variant_label": plan.label,
        "variant_kind": plan.kind,
        "mutation_text": plan.user_input,
        "base_summary": (
            None
            if plan.kind == "entity"
            else base_summary.model_dump(mode="json")
        ),
        "variant_summary": variant_summary.model_dump(mode="json"),
        "variant_transcript": transcript,
        "usable_evidence": compact_evidence,
    }
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ]
    errors: list[str] = []
    for attempt in range(3):
        if budget is not None:
            budget.reserve_judge_call()
        attempt_messages = list(messages)
        if attempt:
            attempt_messages.append({
                "role": "system",
                "content": "The prior response was invalid. Return only the exact JSON object.",
            })
        result = call_research_chat(
            api_key,
            provider_url,
            attempt_messages,
            model=model,
            timeout_s=60,
            max_tokens=4096,
            temperature=temperature,
            response_format_json=True,
        )
        if result.get("error"):
            errors.append(str(result["error"]))
            continue
        raw_reply = str(result.get("reply") or "")
        parsed = _parse_json_object(raw_reply)
        if parsed is None:
            errors.append(f"invalid JSON ({_response_shape(raw_reply)})")
            continue
        try:
            observation = VariantJudgeObservation.model_validate(parsed)
        except ValueError as exc:
            errors.append(f"schema validation: {exc}")
            continue
        if plan.kind == "counterfactual":
            if observation.counterfactual_applied is None:
                errors.append("counterfactual_applied is required for a counterfactual")
                continue
        elif observation.semantic_equivalent is None:
            errors.append("semantic_equivalent is required for this variant kind")
            continue
        return observation
    raise RuntimeError(
        f"variant judge failed after 3 attempts for {case.case_id}/{plan.label}: "
        f"{errors[-1]}"
    )


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


def _response_shape(text: str) -> str:
    """Return non-content diagnostics for an invalid provider response."""

    stripped = text.strip()
    return (
        f"length={len(text)},contains_object_start={'{' in text},"
        f"starts_fence={stripped.startswith('```')}"
    )


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


def build_run_manifest(
    *,
    corpus: SemanticCaseCorpus,
    corpus_path: Path,
    dialogue_model: str,
    judge_model: str,
    provider_url: str,
    dialogue_temperature: float,
    judge_temperature: float,
    variant_seed: int,
    variant_limit: int,
    judge_call_limit: int,
    wall_time_limit_seconds: float,
    created_at: str | None = None,
) -> SemanticRunManifest:
    selector = PromptSelector()
    profile = selector.research_dialogue_profile()
    rendered_prompt = selector.build_research_dialogue_prompt()
    provider_host = urlparse(provider_url).hostname
    if not provider_host:
        raise ValueError("DEEPSEEK_BASE_URL must contain an explicit hostname")
    variant_matrix = select_variant_matrix(
        corpus,
        seed=variant_seed,
        variant_limit=variant_limit,
    )
    selected_variants = {
        case.case_id: [plan.label for plan in variant_matrix[case.case_id]]
        for case in corpus.cases
    }
    return SemanticRunManifest(
        commit_sha=_git_head_sha(),
        dialogue_model=dialogue_model,
        judge_model=judge_model,
        judge_independent=judge_model != dialogue_model,
        provider_host=provider_host.lower(),
        prompt_id=profile.prompt_id,
        prompt_version=profile.prompt_version,
        prompt_sha256=_sha256_text(rendered_prompt),
        corpus_sha256=_sha256_file(corpus_path),
        dialogue_temperature=dialogue_temperature,
        judge_temperature=judge_temperature,
        variant_seed=variant_seed,
        variant_limit=variant_limit,
        variant_count=sum(len(labels) for labels in selected_variants.values()),
        selected_variants=selected_variants,
        judge_call_limit=judge_call_limit,
        wall_time_limit_seconds=wall_time_limit_seconds,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
    )


def ensure_suite_manifest(
    suite_dir: Path,
    expected: SemanticRunManifest,
    *,
    resuming: bool,
) -> Path:
    path = suite_dir / "semantic_run_manifest.json"
    if path.is_file():
        existing = SemanticRunManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if _manifest_fingerprint(existing) != _manifest_fingerprint(expected):
            raise ValueError("suite manifest fingerprint does not match this run")
        return path
    if resuming:
        raise ValueError("suite manifest is required when resuming a prior run")
    _write_json_atomic(path, expected.model_dump(mode="json"))
    return path


def _manifest_fingerprint(manifest: SemanticRunManifest) -> dict[str, Any]:
    return manifest.model_dump(mode="json", exclude={"created_at"})


def _git_head_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument(
        "--model",
        default=os.environ.get("AUTOAD_DIALOGUE_MODEL", "deepseek-v4-flash"),
        help="Production dialogue model used by ResearchDialogueAgent.",
    )
    parser.add_argument(
        "--judge-model",
        default=os.environ.get("AUTOAD_JUDGE_MODEL", ""),
        help="Semantic judge model; defaults to --model when omitted.",
    )
    parser.add_argument(
        "--dialogue-temperature",
        type=float,
        default=0.0,
        help="Dialogue sampling temperature used only by this benchmark.",
    )
    parser.add_argument(
        "--judge-temperature",
        type=float,
        default=0.0,
        help="Judge sampling temperature used only by this benchmark.",
    )
    parser.add_argument("--seed", type=int, default=0, help="Stable variant sampling seed.")
    parser.add_argument(
        "--variant-limit",
        type=int,
        default=0,
        help="Maximum variants to execute; 0 means the complete 36-variant matrix.",
    )
    parser.add_argument(
        "--judge-call-limit",
        type=int,
        default=0,
        help="Maximum top-level semantic Judge invocations; 0 disables the limit.",
    )
    parser.add_argument(
        "--wall-time-limit",
        type=float,
        default=0.0,
        help="Maximum suite wall time in seconds; 0 disables the limit.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show per-case progress without printing raw prompts or provider responses.",
    )
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
    if args.variant_limit < 0:
        raise SystemExit("--variant-limit must be non-negative")
    if args.judge_call_limit < 0:
        raise SystemExit("--judge-call-limit must be non-negative")
    if args.wall_time_limit < 0:
        raise SystemExit("--wall-time-limit must be non-negative")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    provider_url = os.environ.get("DEEPSEEK_BASE_URL", "")
    if not api_key or not provider_url:
        raise SystemExit("DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL are required")
    corpus = load_corpus(args.rubric)
    judge_model = args.judge_model or args.model
    manifest = build_run_manifest(
        corpus=corpus,
        corpus_path=args.rubric,
        dialogue_model=args.model,
        judge_model=judge_model,
        provider_url=provider_url,
        dialogue_temperature=args.dialogue_temperature,
        judge_temperature=args.judge_temperature,
        variant_seed=args.seed,
        variant_limit=args.variant_limit,
        judge_call_limit=args.judge_call_limit,
        wall_time_limit_seconds=args.wall_time_limit,
    )
    variant_matrix = select_variant_matrix(
        corpus,
        seed=args.seed,
        variant_limit=args.variant_limit,
    )
    resuming = args.suite_dir is not None
    if args.suite_dir is not None:
        suite_dir = args.suite_dir
        if not suite_dir.is_dir():
            raise SystemExit(f"suite directory not found: {suite_dir}")
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suite_dir = args.runs_root / f"semantic_acceptance_{timestamp}"
        suite_dir.mkdir(parents=True, exist_ok=False)
    try:
        manifest_path = ensure_suite_manifest(
            suite_dir,
            manifest,
            resuming=resuming,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    completed_dir = suite_dir / "completed_observations"
    completed_dir.mkdir(exist_ok=True)
    budget = SemanticRunBudget(
        judge_call_limit=args.judge_call_limit,
        wall_time_limit_seconds=args.wall_time_limit,
    )
    observations: list[CaseRuntimeObservation] = []
    budget_failure = ""
    run_failure = ""
    try:
        for case in corpus.cases:
            completed_path = completed_dir / f"{case.case_id}.json"
            variants_path = completed_dir / f"{case.case_id}_variants.json"
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
                if args.verbose:
                    print(
                        f"[semantic] reused {case.case_id}: score={partial['score']} "
                        f"vetoes={len(partial['veto_failures'])}",
                        flush=True,
                    )
                if reusable_path != completed_path:
                    completed_path.write_text(
                        reusable_path.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
                if not variants_path.is_file():
                    _write_json_atomic(
                        variants_path,
                        [
                            result.model_dump(mode="json")
                            for result in observation.variant_results
                        ],
                    )
                continue
            if args.verbose:
                print(f"[semantic] running {case.case_id}", flush=True)
            case_run_dir = _next_case_run_dir(suite_dir, case.case_id)
            observation = run_case(
                case,
                run_dir=case_run_dir,
                api_key=api_key,
                provider_url=provider_url,
                model=args.model,
                judge_model=judge_model,
                dialogue_temperature=args.dialogue_temperature,
                judge_temperature=args.judge_temperature,
                budget=budget,
                variant_plans=variant_matrix[case.case_id],
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
            _write_json_atomic(
                variants_path,
                [result.model_dump(mode="json") for result in observation.variant_results],
            )
            partial = score_case(case, observation)
            if args.verbose:
                print(
                    f"[semantic] {case.case_id}: score={partial['score']} "
                    f"vetoes={len(partial['veto_failures'])}",
                    flush=True,
                )
    except SemanticBudgetExceeded as exc:
        budget_failure = exc.reason
    except RuntimeError:
        run_failure = f"case_runtime_error:{case.case_id}"

    case_coverage_complete = len(observations) == len(corpus.cases)
    if case_coverage_complete:
        report = score_report(
            corpus,
            observations,
            judge_independent=manifest.judge_independent,
        )
    else:
        report = {
            "schema_version": 1,
            "case_count": len(corpus.cases),
            "completed_case_count": len(observations),
            "average_score": 0.0,
            "minimum_score": 0.0,
            "veto_failure_count": 0,
            "judge_independent": manifest.judge_independent,
            "release_gate_passed": False,
            "results": [],
        }
    coverage_complete = (
        case_coverage_complete
        and bool(report.get("variant_coverage_complete", False))
    )
    report["coverage_complete"] = coverage_complete
    report["budget"] = {
        "status": budget_failure or "within_limit",
        "dialogue_calls": budget.dialogue_calls,
        "judge_calls": budget.judge_calls,
        "judge_call_limit": budget.judge_call_limit,
        "wall_time_limit_seconds": budget.wall_time_limit_seconds,
        "elapsed_seconds": round(budget.elapsed_seconds(), 3),
    }
    if budget_failure:
        report["release_gate_passed"] = False
    if run_failure:
        report["run_failure"] = run_failure
        report["release_gate_passed"] = False
    if not coverage_complete:
        report["release_gate_passed"] = False
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
    passed_cases = sum(
        1 for item in report.get("results", []) if item.get("passes_case_threshold")
    )
    status = "PASS" if report["release_gate_passed"] else "FAIL"
    detail = ""
    if not report["release_gate_passed"]:
        failed_ids = [
            str(item.get("case_id"))
            for item in report.get("results", [])
            if not item.get("passes_case_threshold")
        ]
        reasons = [*failed_ids]
        if not manifest.judge_independent:
            reasons.append("judge_not_independent")
        if budget_failure:
            reasons.append(budget_failure)
        if run_failure:
            reasons.append(run_failure)
        if not coverage_complete:
            reasons.append("coverage_incomplete")
        if reasons:
            detail = ", detail: " + ", ".join(reasons)
    print(
        f"{status} ({passed_cases}/{len(corpus.cases)}{detail}) "
        f"report={report_path} manifest={manifest_path}"
    )
    if args.verbose:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
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
