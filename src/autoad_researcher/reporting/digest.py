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
    execution_status: str | None = None
    attempt_count: int = 0
    failed_attempt_count: int = 0
    non_comparable_attempt_count: int = 0
    champion: dict
    stop_decision: dict
    uncertainties: list[str]


def build_report_digest(*, report_id: str, facts: ExperimentReportFactsV1) -> ReportDigest:
    status = facts.repository_and_environment.get("status")
    return ReportDigest(
        report_id=report_id,
        facts_content_sha256=facts_content_sha256(facts),
        research_objective=facts.research_objective,
        execution_status=status if isinstance(status, str) else None,
        attempt_count=len(facts.attempts),
        failed_attempt_count=len(facts.failed_attempts),
        non_comparable_attempt_count=len(facts.non_comparable_attempts),
        champion=facts.candidate_and_champion,
        stop_decision=facts.stop_decision,
        uncertainties=facts.uncertainties,
    )
