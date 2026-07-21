"""Deterministic publication checks for structured narrative and Facts bindings."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeSectionsV1

REQUIRED_SECTIONS = {"summary", "interpretation", "limitations", "next_steps"}
REPORT_VALIDATOR_VERSION = "v2"
_PLACEHOLDER = re.compile(r"\{\{fact:([A-Za-z0-9_.-]+)\}\}")


class ReportValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    passed: bool
    errors: list[str]
    warnings: list[str]


def validate_report(*, facts: ExperimentReportFactsV1, evidence: EvidenceIndex, narrative: NarrativeSectionsV1) -> ReportValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    sections = {item.section_id: item for item in narrative.sections}
    if set(sections) != REQUIRED_SECTIONS or len(sections) != len(narrative.sections):
        errors.append("required Narrative sections must appear exactly once")
    claims = {item.claim_id: item for item in narrative.claims}
    if len(claims) != len(narrative.claims):
        errors.append("Narrative claim IDs must be unique")
    evidence_ids = {item.evidence_id for item in evidence.entries}
    for section in narrative.sections:
        for paragraph in section.paragraphs:
            if paragraph.paragraph_kind in {"interpretation", "limitation"} and not paragraph.claim_ids:
                errors.append(f"paragraph {paragraph.paragraph_id} requires a claim ID")
            unknown_claims = set(paragraph.claim_ids).difference(claims)
            if unknown_claims:
                errors.append(f"paragraph {paragraph.paragraph_id} references unknown claim IDs")
            _validate_placeholders(facts, paragraph.prose_template, f"paragraph {paragraph.paragraph_id}", errors)
    for claim in narrative.claims:
        unknown_evidence = set(claim.evidence_ids).difference(evidence_ids)
        if unknown_evidence:
            errors.append(f"claim {claim.claim_id} references unknown Evidence IDs")
        for fact_ref in claim.fact_refs:
            if _resolve_fact(facts, fact_ref) is _MISSING:
                errors.append(f"claim {claim.claim_id} references an unknown Fact")
        _validate_placeholders(facts, claim.statement_template, f"claim {claim.claim_id}", errors)
    if facts.non_comparable_attempts:
        warnings.append("Non-comparable Attempts remain in deterministic result tables.")
    if not facts.failed_attempts and not facts.non_comparable_attempts:
        warnings.append("No failed or non-comparable Attempt is present in the frozen Facts")
    return ReportValidationResult(passed=not errors, errors=errors, warnings=warnings)


_MISSING = object()


def resolve_fact(facts: ExperimentReportFactsV1, path: str):
    value = _resolve_fact(facts, path)
    if value is _MISSING:
        raise ValueError(f"unknown report Fact placeholder: {path}")
    if isinstance(value, list):
        return "；".join(str(item) for item in value) if value else "未记录"
    if value is None:
        return "未记录"
    return str(value)


def _validate_placeholders(facts: ExperimentReportFactsV1, template: str, location: str, errors: list[str]) -> None:
    for path in _PLACEHOLDER.findall(template):
        if _resolve_fact(facts, path) is _MISSING:
            errors.append(f"{location} contains an unknown Fact placeholder")


def _resolve_fact(facts: ExperimentReportFactsV1, path: str):
    value: object = facts.model_dump(mode="json")
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return _MISSING
    return value
