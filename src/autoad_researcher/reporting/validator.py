"""Deterministic publication checks for report narrative and Markdown."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeSectionsV1

REQUIRED_SECTIONS = {"summary", "interpretation", "limitations", "next_steps"}


class ReportValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    passed: bool
    errors: list[str]
    warnings: list[str]


def validate_report(*, facts: ExperimentReportFactsV1, evidence: EvidenceIndex, narrative: NarrativeSectionsV1) -> ReportValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    sections = {item.section_id: item for item in narrative.sections}
    if set(sections) != REQUIRED_SECTIONS or len(sections) != len(narrative.sections):
        errors.append("required Narrative sections must appear exactly once")
    evidence_ids = {item.evidence_id for item in evidence.entries}
    attempt_ids = {item.get("attempt_id") for item in facts.attempts}
    for section in narrative.sections:
        unknown = set(section.evidence_ids) - evidence_ids
        if unknown:
            errors.append(f"section {section.section_id} references unknown Evidence IDs")
        for attempt_id in attempt_ids:
            if isinstance(attempt_id, str) and attempt_id in section.text and attempt_id not in {item.attempt_id for item in evidence.entries if item.attempt_id}:
                errors.append(f"section {section.section_id} names an Attempt without Evidence")
    if facts.non_comparable_attempts:
        text = " ".join(item.text.lower() for item in narrative.sections)
        if "improvement" in text or "提升" in text:
            warnings.append("Narrative contains improvement language while non-comparable attempts exist; deterministic tables remain authoritative")
    if not facts.failed_attempts and not facts.non_comparable_attempts:
        warnings.append("No failed or non-comparable Attempt is present in the frozen Facts")
    return ReportValidationResult(passed=not errors, errors=errors, warnings=warnings)
