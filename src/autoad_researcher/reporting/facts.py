"""Pure-code assembly of report facts from an already frozen source inventory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.experiment.finalizer import OutcomeCard
from autoad_researcher.experiment.scientific_assessment import ScientificAssessment
from autoad_researcher.reporting.models import ReportSnapshot
from autoad_researcher.reporting.snapshot import canonical_sha256, resolve_run_relative_file, sha256_file
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2


class ExperimentReportFactsV1(BaseModel):
    """A report-ready projection; scientific values are copied, never recomputed."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    research_objective: dict[str, Any]
    evaluation_contract: dict[str, Any]
    repository_and_environment: dict[str, Any]
    baseline: list[dict[str, Any]]
    candidate_and_champion: dict[str, Any]
    ideas: list[dict[str, Any]]
    attempts: list[dict[str, Any]]
    primary_metrics: list[dict[str, Any]]
    guardrail_metrics: list[dict[str, Any]]
    validity: list[dict[str, Any]]
    failed_attempts: list[dict[str, Any]]
    non_comparable_attempts: list[dict[str, Any]]
    stop_decision: dict[str, Any]
    cost_summary: dict[str, Any]
    uncertainties: list[str]
    source_refs: list[ArtifactReferenceV2]


def assemble_facts(run_dir: Path, *, snapshot: ReportSnapshot) -> ExperimentReportFactsV1:
    """Read only references in ``snapshot`` and expose missing facts explicitly."""

    raw = _verified_snapshot_objects(run_dir, snapshot)
    session = _one(raw, "experiment_session")
    attempts = _attempt_facts(raw)
    failed = [item for item in attempts if item.get("runtime_status") in {"FAILED", "TIMED_OUT", "CANCELLED", "LOST"}]
    non_comparable = [
        item
        for item in attempts
        if isinstance(item.get("outcome"), dict) and item["outcome"].get("evaluation_status") == "NON_COMPARABLE"
    ]
    baseline = [item for item in attempts if item.get("attempt_purpose") == "baseline"]
    uncertainties = _uncertainties(session, attempts, non_comparable)
    return ExperimentReportFactsV1(
        run_id=snapshot.run_id,
        session_id=snapshot.session_id,
        research_objective={"task_ref": session.get("task_ref") if session else None, "status": "available" if session else "missing"},
        evaluation_contract={"ref": snapshot.evaluation_contract_ref, "status": "referenced" if snapshot.evaluation_contract_ref else "missing"},
        repository_and_environment={
            "repository_ref": session.get("repository_ref") if session else None,
            "environment_snapshot_ref": snapshot.environment_snapshot_ref,
            "status": session.get("status") if session else "missing",
        },
        baseline=baseline,
        candidate_and_champion={"status": "not_materialized", "candidates": []},
        ideas=[],
        attempts=attempts,
        primary_metrics=_metric_facts(attempts, "primary"),
        guardrail_metrics=_metric_facts(attempts, "guardrail"),
        validity=[item["assessment"] for item in attempts if isinstance(item.get("assessment"), dict)],
        failed_attempts=failed,
        non_comparable_attempts=non_comparable,
        stop_decision={"status": "unknown", "reason": "StopDecision is not in this snapshot"},
        cost_summary={"status": "unknown", "reason": "CognitiveCostSummary is not in this snapshot"},
        uncertainties=uncertainties,
        source_refs=snapshot.source_refs,
    )


def facts_content_sha256(facts: ExperimentReportFactsV1) -> str:
    return canonical_sha256(facts.model_dump(mode="json"))


def _verified_snapshot_objects(run_dir: Path, snapshot: ReportSnapshot) -> list[tuple[ArtifactReferenceV2, dict[str, Any]]]:
    result: list[tuple[ArtifactReferenceV2, dict[str, Any]]] = []
    for reference in snapshot.source_refs:
        path = resolve_run_relative_file(run_dir, reference.locator)
        if sha256_file(path) != reference.sha256:
            raise ValueError("snapshot artifact SHA-256 no longer matches")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("snapshot artifact is not readable JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("snapshot JSON artifact must be an object")
        result.append((reference, value))
    return result


def _one(raw: list[tuple[ArtifactReferenceV2, dict[str, Any]]], artifact_type: str) -> dict[str, Any]:
    return next((value for reference, value in raw if reference.artifact_type == artifact_type), {})


def _attempt_facts(raw: list[tuple[ArtifactReferenceV2, dict[str, Any]]]) -> list[dict[str, Any]]:
    attempts: dict[str, dict[str, Any]] = {}
    for reference, value in raw:
        attempt_id = next((part for part in reference.artifact_id.split(":") if part.startswith("attempt_")), None)
        if attempt_id is None:
            continue
        entry = attempts.setdefault(attempt_id, {"attempt_id": attempt_id})
        if reference.artifact_type == "experiment_attempt":
            entry.update(value)
        elif reference.artifact_type == "outcome_card":
            card = OutcomeCard.model_validate(value)
            entry["outcome"] = card.model_dump(mode="json", exclude_none=True)
        elif reference.artifact_type == "scientific_assessment":
            assessment = ScientificAssessment.model_validate(value)
            entry["assessment"] = assessment.model_dump(mode="json", exclude_none=True)
    return [attempts[key] for key in sorted(attempts)]


def _metric_facts(attempts: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    values = []
    for item in attempts:
        outcome = item.get("outcome")
        if isinstance(outcome, dict) and isinstance(outcome.get("metrics"), dict):
            values.append({"attempt_id": item["attempt_id"], "kind": kind, "metrics": outcome["metrics"]})
    return values


def _uncertainties(session: dict[str, Any], attempts: list[dict[str, Any]], non_comparable: list[dict[str, Any]]) -> list[str]:
    messages = []
    if not session:
        messages.append("ExperimentSession is missing from the frozen source inventory.")
    if not attempts:
        messages.append("No ExperimentAttempt is present in the frozen source inventory.")
    if non_comparable:
        messages.append("One or more attempts are non-comparable under their recorded evaluation status.")
    if any("outcome" not in item for item in attempts):
        messages.append("One or more attempts have no frozen OutcomeCard.")
    return messages
