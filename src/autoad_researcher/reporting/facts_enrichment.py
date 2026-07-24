"""Typed enrichment of Facts from verified snapshot artifacts only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.experiment.cost_summary import CognitiveCostSummary
from autoad_researcher.experiment.evaluation_contract import EvaluationContract
from autoad_researcher.experiment.idea_tree import IdeaTree
from autoad_researcher.experiment.promotion import CandidateSnapshot
from autoad_researcher.experiment.stop_policy import StopDecision
from autoad_researcher.environments.snapshot import EnvironmentSnapshot
from autoad_researcher.schemas.execution import ResourceUsageReport
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.models import ReportSnapshot
from autoad_researcher.reporting.snapshot import read_verified_snapshot_artifact


def enrich_facts(run_dir: Path, *, snapshot: ReportSnapshot, facts: ExperimentReportFactsV1) -> ExperimentReportFactsV1:
    """Add typed contract/control-plane projections without reinterpreting outcomes."""

    values = _values_by_type(run_dir, snapshot)
    contract = _parse_one(values, "evaluation_contract", EvaluationContract)
    tree = _parse_one(values, "idea_tree", IdeaTree)
    stop = _parse_one(values, "stop_decision", StopDecision)
    cost = _parse_one(values, "cognitive_cost_summary", CognitiveCostSummary)
    environment = _parse_one(values, "environment_snapshot", EnvironmentSnapshot)
    candidates = _parse_all(values, "candidate_snapshot", CandidateSnapshot)
    pointers = _one(values, "champion_pointers")
    resources = _parse_all(values, "resource_usage_report", ResourceUsageReport)

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
    repository_and_environment = dict(facts.repository_and_environment)
    repository_and_environment["environment_snapshot"] = _environment_projection(
        environment,
        ref=snapshot.environment_snapshot_ref,
    )
    uncertainties = list(facts.uncertainties)
    if contract is None:
        uncertainties.append("EvaluationContract is not available in the frozen source inventory.")
    if stop is None:
        uncertainties.append("StopDecision is not available in the frozen source inventory.")
    return facts.model_copy(
        update={
            "evaluation_contract": evaluation_contract,
            "repository_and_environment": repository_and_environment,
            "candidate_and_champion": champion,
            "ideas": ideas,
            "primary_metrics": primary,
            "guardrail_metrics": guardrails,
            "stop_decision": stop_value,
            "cognitive_cost_summary": cost_value,
            "compute_resource_summary": _resource_summary(resources),
            "uncertainties": sorted(set(uncertainties)),
        }
    )


def _environment_projection(
    environment: EnvironmentSnapshot | None,
    *,
    ref: str | None,
) -> dict[str, Any]:
    """Expose the registered observed snapshot without leaking its local path."""

    if environment is None:
        return {"status": "missing", "ref": ref}
    return {
        "status": "available",
        "ref": ref,
        "snapshot": environment.model_dump(mode="json", exclude={"environment_path"}),
    }


def _resource_summary(items: list[ResourceUsageReport]) -> dict[str, Any]:
    if not items:
        return {"status": "unknown", "reason": "No registered ResourceUsageReport is in this snapshot"}
    gpu_hours = [item.actual_gpu_hours for item in items if item.actual_gpu_hours is not None]
    measured = [item for item in items if item.measurement_kind == "measured"]
    return {
        "status": "available",
        "report_count": len(items),
        "measurement_kinds": sorted({item.measurement_kind for item in items}),
        "total_gpu_hours": sum(gpu_hours) if gpu_hours else None,
        "max_gpu_count_used": max((item.gpu_count_used or 0 for item in items), default=0),
        "fully_measured_report_count": len(measured),
        "reports": [item.model_dump(mode="json") for item in items],
    }


def _values_by_type(run_dir: Path, snapshot: ReportSnapshot) -> dict[str, list[dict[str, Any]]]:
    values: dict[str, list[dict[str, Any]]] = {key: list(items) for key, items in snapshot.frozen_control_plane.items()}
    for reference in snapshot.source_refs:
        if reference.artifact_type in values:
            continue
        raw = read_verified_snapshot_artifact(run_dir, reference)
        values.setdefault(reference.artifact_type, []).append(raw)
    return values


def _one(values: dict[str, list[dict[str, Any]]], kind: str) -> dict[str, Any] | None:
    items = values.get(kind, [])
    return items[0] if items else None


def _parse_one(values, kind, model):
    raw = _one(values, kind)
    return None if raw is None else model.model_validate(raw)


def _parse_all(values, kind, model):
    parsed = []
    for item in values.get(kind, []):
        # Pydantic serializes ResourceUsageReport's computed GPU-hours field,
        # while its strict input schema intentionally derives it.
        value = dict(item)
        if model is ResourceUsageReport:
            value.pop("actual_gpu_hours", None)
        parsed.append(model.model_validate(value))
    return parsed


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
