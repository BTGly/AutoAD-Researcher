"""Typed enrichment of Facts from verified snapshot artifacts only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoad_researcher.experiment.cost_summary import CognitiveCostSummary
from autoad_researcher.experiment.evaluation_contract import EvaluationContract
from autoad_researcher.experiment.idea_tree import IdeaTree
from autoad_researcher.experiment.promotion import CandidateSnapshot
from autoad_researcher.experiment.stop_policy import StopDecision
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.models import ReportSnapshot
from autoad_researcher.reporting.snapshot import resolve_run_relative_file, sha256_file


def enrich_facts(run_dir: Path, *, snapshot: ReportSnapshot, facts: ExperimentReportFactsV1) -> ExperimentReportFactsV1:
    """Add typed contract/control-plane projections without reinterpreting outcomes."""

    values = _values_by_type(run_dir, snapshot)
    contract = _parse_one(values, "evaluation_contract", EvaluationContract)
    tree = _parse_one(values, "idea_tree", IdeaTree)
    stop = _parse_one(values, "stop_decision", StopDecision)
    cost = _parse_one(values, "cognitive_cost_summary", CognitiveCostSummary)
    candidates = _parse_all(values, "candidate_snapshot", CandidateSnapshot)
    pointers = _one(values, "champion_pointers")

    evaluation_contract = (
        contract.model_dump(mode="json")
        if contract is not None
        else {"ref": snapshot.evaluation_contract_ref, "status": "missing"}
    )
    primary, guardrails = _metric_projection(facts.attempts, contract)
    champion = {
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "current_by_contract": pointers or {},
        "status": "available" if candidates or pointers else "not_materialized",
    }
    ideas = [] if tree is None else [item.model_dump(mode="json") for item in tree.nodes]
    stop_value = stop.model_dump(mode="json") if stop is not None else {"status": "unknown", "reason": "StopDecision is not in this snapshot"}
    cost_value = cost.model_dump(mode="json") if cost is not None else {"status": "unknown", "reason": "CognitiveCostSummary is not in this snapshot"}
    uncertainties = list(facts.uncertainties)
    if contract is None:
        uncertainties.append("EvaluationContract is not available in the frozen source inventory.")
    if stop is None:
        uncertainties.append("StopDecision is not available in the frozen source inventory.")
    return facts.model_copy(
        update={
            "evaluation_contract": evaluation_contract,
            "candidate_and_champion": champion,
            "ideas": ideas,
            "primary_metrics": primary,
            "guardrail_metrics": guardrails,
            "stop_decision": stop_value,
            "cost_summary": cost_value,
            "uncertainties": sorted(set(uncertainties)),
        }
    )


def _values_by_type(run_dir: Path, snapshot: ReportSnapshot) -> dict[str, list[dict[str, Any]]]:
    values: dict[str, list[dict[str, Any]]] = {}
    for reference in snapshot.source_refs:
        path = resolve_run_relative_file(run_dir, reference.locator)
        if sha256_file(path) != reference.sha256:
            raise ValueError("snapshot artifact SHA-256 no longer matches")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("snapshot artifact is not readable JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("snapshot JSON artifact must be an object")
        values.setdefault(reference.artifact_type, []).append(raw)
    return values


def _one(values: dict[str, list[dict[str, Any]]], kind: str) -> dict[str, Any] | None:
    items = values.get(kind, [])
    return items[0] if items else None


def _parse_one(values, kind, model):
    raw = _one(values, kind)
    return None if raw is None else model.model_validate(raw)


def _parse_all(values, kind, model):
    return [model.model_validate(item) for item in values.get(kind, [])]


def _metric_projection(attempts: list[dict[str, Any]], contract: EvaluationContract | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if contract is None:
        return [], []
    primary: list[dict[str, Any]] = []
    guardrails: list[dict[str, Any]] = []
    for attempt in attempts:
        outcome = attempt.get("outcome")
        metrics = outcome.get("metrics") if isinstance(outcome, dict) else None
        if not isinstance(metrics, dict):
            continue
        attempt_id = attempt["attempt_id"]
        if contract.primary_metric in metrics:
            primary.append({"attempt_id": attempt_id, "metric": contract.primary_metric, "value": metrics[contract.primary_metric]})
        for name in contract.guardrails:
            if name in metrics:
                guardrails.append({"attempt_id": attempt_id, "metric": name, "value": metrics[name]})
    return primary, guardrails
