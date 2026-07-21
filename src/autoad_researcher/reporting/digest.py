"""Small, deterministic report summary for UI and read-only discussion."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.reporting.facts import ExperimentReportFactsV1, facts_content_sha256


class ReportDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    report_id: str = Field(min_length=1)
    facts_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    research_objective: dict
    engineering_status: str | None = None
    execution_status: str | None = None
    scientific_status: str | None = None
    attempt_count: int = 0
    failed_attempt_count: int = 0
    non_comparable_attempt_count: int = 0
    champion: dict
    primary_metrics: list[dict]
    stop_decision: dict
    uncertainties: list[str]


def build_report_digest(*, report_id: str, facts: ExperimentReportFactsV1) -> ReportDigest:
    engineering = facts.repository_and_environment.get("status")
    return ReportDigest(
        report_id=report_id,
        facts_content_sha256=facts_content_sha256(facts),
        research_objective=facts.research_objective,
        engineering_status=engineering if isinstance(engineering, str) else None,
        execution_status=_execution_status(facts),
        scientific_status=_scientific_status(facts),
        attempt_count=len(facts.attempts),
        failed_attempt_count=len(facts.failed_attempts),
        non_comparable_attempt_count=len(facts.non_comparable_attempts),
        champion=facts.candidate_and_champion,
        primary_metrics=facts.primary_metrics,
        stop_decision=facts.stop_decision,
        uncertainties=facts.uncertainties,
    )


def _execution_status(facts: ExperimentReportFactsV1) -> str:
    values = [
        item["outcome"].get("execution_status")
        for item in facts.attempts
        if isinstance(item.get("outcome"), dict) and isinstance(item["outcome"].get("execution_status"), str)
    ]
    if not facts.attempts:
        return "NO_ATTEMPTS"
    if not values:
        return "EVIDENCE_INSUFFICIENT"
    if len(values) != len(facts.attempts):
        return "PARTIAL"
    return values[0] if len(set(values)) == 1 else "MIXED"


def _scientific_status(facts: ExperimentReportFactsV1) -> str:
    values = [item["assessment"] for item in facts.attempts if isinstance(item.get("assessment"), dict)]
    if not facts.attempts:
        return "NO_ATTEMPTS"
    if not values or len(values) != len(facts.attempts):
        return "EVIDENCE_INSUFFICIENT"
    if any(item.get("evaluation_status") == "NON_COMPARABLE" for item in values):
        return "NON_COMPARABLE"
    effects = {item.get("scientific_effect") for item in values}
    return str(next(iter(effects))) if len(effects) == 1 and None not in effects else "MIXED"
