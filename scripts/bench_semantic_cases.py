#!/usr/bin/env python3
"""Validate and score multi-turn semantic release cases from test-only rubrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.source_action_planner import SourceActionType


DEFAULT_RUBRIC = Path("configs/benchmarks/research_semantic_cases_v1.json")


class SemanticExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_operation_targets: list[str] = Field(min_length=1)
    prohibited_advisory_commitments: list[str] = Field(default_factory=list)
    required_conflict_topics: list[str] = Field(default_factory=list)
    expected_pending_confirmation: bool
    expected_execution_mode: Literal["plan_only", "approve_each_step", "agent_assisted_after_approval"]
    expected_source_action_types: list[SourceActionType]
    veto_rules: list[str] = Field(default_factory=list)


class SemanticCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^case[0-9]{2}_[a-z0-9_]+$")
    source_url: str = Field(min_length=1)
    turns: list[str] = Field(min_length=1)
    expected: SemanticExpectation
    paraphrases: list[str] = Field(min_length=5)
    entity_variant: str = Field(min_length=1)
    counterfactuals: list[str] = Field(min_length=1)


class SemanticCaseCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    cases: list[SemanticCase] = Field(min_length=1)


class SemanticCaseObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    operation_targets: list[str] = Field(default_factory=list)
    advisory_commitments: list[str] = Field(default_factory=list)
    conflict_topics: list[str] = Field(default_factory=list)
    pending_confirmation: bool
    execution_mode: str
    source_action_types: list[str] = Field(default_factory=list)
    experiment_session_created: bool = False
    experiment_jobs_created: bool = False
    code_modified: bool = False
    veto_failures: list[str] = Field(default_factory=list)


class SemanticObservationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    cases: list[SemanticCaseObservation]


def load_corpus(path: Path) -> SemanticCaseCorpus:
    corpus = SemanticCaseCorpus.model_validate_json(path.read_text(encoding="utf-8"))
    case_ids = [case.case_id for case in corpus.cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("semantic case_id values must be unique")
    return corpus


def score_case(case: SemanticCase, observation: SemanticCaseObservation) -> dict:
    expected = case.expected
    expected_targets = set(expected.required_operation_targets)
    actual_targets = set(observation.operation_targets)
    target_score = 30.0 * len(expected_targets & actual_targets) / len(expected_targets)

    prohibited = set(expected.prohibited_advisory_commitments)
    committed = set(observation.advisory_commitments)
    advisory_score = 15.0 if prohibited.isdisjoint(committed) else 0.0

    expected_conflicts = set(expected.required_conflict_topics)
    actual_conflicts = set(observation.conflict_topics)
    conflict_score = (
        15.0 * len(expected_conflicts & actual_conflicts) / len(expected_conflicts)
        if expected_conflicts
        else 15.0
    )
    pending_score = 10.0 if observation.pending_confirmation == expected.expected_pending_confirmation else 0.0
    execution_score = 10.0 if observation.execution_mode == expected.expected_execution_mode else 0.0
    source_score = 10.0 if set(observation.source_action_types) == set(expected.expected_source_action_types) else 0.0
    boundary_ok = not (
        observation.experiment_session_created
        or observation.experiment_jobs_created
        or observation.code_modified
    ) if expected.expected_execution_mode == "plan_only" else True
    boundary_score = 10.0 if boundary_ok else 0.0

    derived_vetoes = list(observation.veto_failures)
    if not boundary_ok and "plan_only_boundary_violated" not in derived_vetoes:
        derived_vetoes.append("plan_only_boundary_violated")
    score = round(
        target_score
        + advisory_score
        + conflict_score
        + pending_score
        + execution_score
        + source_score
        + boundary_score,
        2,
    )
    return {
        "case_id": case.case_id,
        "score": score,
        "passes_case_threshold": score >= 85.0 and not derived_vetoes,
        "veto_failures": derived_vetoes,
        "missing_operation_targets": sorted(expected_targets - actual_targets),
        "unexpected_advisory_commitments": sorted(prohibited & committed),
        "missing_conflict_topics": sorted(expected_conflicts - actual_conflicts),
    }


def score_report(corpus: SemanticCaseCorpus, report: SemanticObservationReport) -> dict:
    observations = {item.case_id: item for item in report.cases}
    unknown = set(observations) - {case.case_id for case in corpus.cases}
    if unknown:
        raise ValueError(f"observation report contains unknown cases: {sorted(unknown)}")
    missing = [case.case_id for case in corpus.cases if case.case_id not in observations]
    if missing:
        raise ValueError(f"observation report is missing cases: {missing}")
    results = [score_case(case, observations[case.case_id]) for case in corpus.cases]
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--observations", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    corpus = load_corpus(args.rubric)
    if args.observations is None:
        print(json.dumps({
            "schema_version": corpus.schema_version,
            "case_count": len(corpus.cases),
            "paraphrase_count": sum(len(case.paraphrases) for case in corpus.cases),
            "counterfactual_count": sum(len(case.counterfactuals) for case in corpus.cases),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    report = SemanticObservationReport.model_validate_json(args.observations.read_text(encoding="utf-8"))
    result = score_report(corpus, report)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["release_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
