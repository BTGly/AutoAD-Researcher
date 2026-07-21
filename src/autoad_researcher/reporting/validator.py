"""Deterministic publication checks for structured narrative and Facts bindings."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeSectionsV1

REQUIRED_SECTIONS = {"summary", "interpretation", "limitations", "next_steps"}
REPORT_VALIDATOR_VERSION = "v3"
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
    _validate_evidence_bindings(facts, evidence, errors)
    attempt_by_id = {item["attempt_id"] for item in facts.attempts if isinstance(item.get("attempt_id"), str)}
    for section in narrative.sections:
        for paragraph in section.paragraphs:
            if paragraph.paragraph_kind in {"interpretation", "limitation"} and not paragraph.claim_ids:
                errors.append(f"paragraph {paragraph.paragraph_id} requires a claim ID")
            unknown_claims = set(paragraph.claim_ids).difference(claims)
            if unknown_claims:
                errors.append(f"paragraph {paragraph.paragraph_id} references unknown claim IDs")
            _validate_placeholders(facts, paragraph.prose_template, f"paragraph {paragraph.paragraph_id}", errors)
            if paragraph.paragraph_kind in {"interpretation", "limitation"}:
                bound_facts = {
                    fact_ref
                    for claim_id in paragraph.claim_ids
                    if claim_id in claims
                    for fact_ref in claims[claim_id].fact_refs
                }
                unbound = set(_PLACEHOLDER.findall(paragraph.prose_template)).difference(bound_facts)
                if unbound:
                    errors.append(f"paragraph {paragraph.paragraph_id} template placeholders are not bound by its Claims")
    for claim in narrative.claims:
        unknown_evidence = set(claim.evidence_ids).difference(evidence_ids)
        if unknown_evidence:
            errors.append(f"claim {claim.claim_id} references unknown Evidence IDs")
        if not claim.fact_refs:
            errors.append(f"claim {claim.claim_id} must bind at least one Fact")
        for fact_ref in claim.fact_refs:
            if _resolve_fact(facts, fact_ref) is _MISSING:
                errors.append(f"claim {claim.claim_id} references an unknown Fact")
        if not claim.evidence_ids:
            errors.append(f"claim {claim.claim_id} binds Facts without Evidence IDs")
        _validate_claim_fact_evidence(claim, evidence, errors)
        inferred_attempts = _attempt_ids_from_fact_refs(facts, claim.fact_refs)
        if set(claim.attempt_ids) != inferred_attempts:
            errors.append(f"claim {claim.claim_id} Attempt IDs must match its Fact bindings")
        unknown_attempts = set(claim.attempt_ids).difference(attempt_by_id)
        if unknown_attempts:
            errors.append(f"claim {claim.claim_id} references unknown Attempt IDs")
        if claim.assertion_scope == "scientific_assessment" and not claim.attempt_ids:
            errors.append(f"scientific claim {claim.claim_id} must bind an Attempt")
        if claim.assertion_scope == "scientific_assessment" and set(claim.asserted_scientific_effects) != set(claim.attempt_ids):
            errors.append(f"scientific claim {claim.claim_id} must assert every bound Attempt effect")
        for attempt_id, asserted_effect in claim.asserted_scientific_effects.items():
            if attempt_id not in attempt_by_id:
                errors.append(f"claim {claim.claim_id} asserts an unknown Attempt ID")
                continue
            attempt = next(item for item in facts.attempts if item.get("attempt_id") == attempt_id)
            # Legacy frozen reports may not yet contain a dedicated assessment
            # projection. Prefer it when present; do not reinterpret its values.
            assessment = attempt.get("assessment") if isinstance(attempt.get("assessment"), dict) else attempt.get("outcome") if isinstance(attempt.get("outcome"), dict) else {}
            if assessment.get("evaluation_status") == "NON_COMPARABLE":
                errors.append(f"claim {claim.claim_id} asserts scientific effect for a non-comparable Attempt")
            elif assessment.get("scientific_effect") != asserted_effect:
                errors.append(f"claim {claim.claim_id} scientific effect conflicts with frozen Facts")
        _validate_placeholders(facts, claim.statement_template, f"claim {claim.claim_id}", errors)
        unresolved = set(_PLACEHOLDER.findall(claim.statement_template)).difference(claim.fact_refs)
        if unresolved:
            errors.append(f"claim {claim.claim_id} template placeholders are not bound Facts")
    if facts.non_comparable_attempts:
        warnings.append("Non-comparable Attempts remain in deterministic result tables.")
    if not facts.failed_attempts and not facts.non_comparable_attempts:
        warnings.append("No failed or non-comparable Attempt is present in the frozen Facts")
    return ReportValidationResult(passed=not errors, errors=errors, warnings=warnings)


def _validate_claim_fact_evidence(claim, evidence: EvidenceIndex, errors: list[str]) -> None:
    by_id = {item.evidence_id: item for item in evidence.entries}
    for fact_ref in claim.fact_refs:
        matching = {item.evidence_id for item in evidence.entries if fact_ref in item.fact_refs}
        if not matching:
            errors.append(f"claim {claim.claim_id} Fact has no registered field Evidence: {fact_ref}")
        elif not matching.intersection(claim.evidence_ids):
            errors.append(f"claim {claim.claim_id} Evidence does not correspond to Fact: {fact_ref}")
    if any(item not in by_id for item in claim.evidence_ids):
        return


def _attempt_ids_from_fact_refs(facts: ExperimentReportFactsV1, refs: list[str]) -> set[str]:
    result: set[str] = set()
    for ref in refs:
        parts = ref.split(".")
        if len(parts) < 2 or parts[0] != "attempts" or not parts[1].isdigit():
            continue
        index = int(parts[1])
        if index < len(facts.attempts):
            attempt_id = facts.attempts[index].get("attempt_id")
            if isinstance(attempt_id, str):
                result.add(attempt_id)
    return result


def _validate_evidence_bindings(facts: ExperimentReportFactsV1, evidence: EvidenceIndex, errors: list[str]) -> None:
    source_refs = {item.artifact_id: item for item in facts.source_refs}
    evidence_ids: set[str] = set()
    for entry in evidence.entries:
        if entry.evidence_id in evidence_ids:
            errors.append("Evidence IDs must be unique")
        evidence_ids.add(entry.evidence_id)
        source = source_refs.get(entry.source_object_id)
        if source is None:
            errors.append(f"Evidence {entry.evidence_id} references an unknown snapshot object")
            continue
        if entry.field_path != "$" and not entry.field_path.strip("."):
            errors.append(f"Evidence {entry.evidence_id} has an invalid field path")
        if entry.evidence_kind.startswith("frozen_"):
            if not entry.artifact_ref.artifact_type.startswith("frozen_"):
                errors.append(f"Evidence {entry.evidence_id} has an invalid frozen reference")
        elif entry.artifact_ref != source:
            errors.append(f"Evidence {entry.evidence_id} does not preserve its source SHA binding")


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
