"""Evidence and claim validator for Paper Intelligence."""

from dataclasses import dataclass, field
from typing import Literal

from autoad_researcher.paper_intelligence.models import (
    PaperClaim,
    PaperMentionedCandidate,
)


@dataclass
class ClaimValidationIssue:
    """A single validation issue found in a claim."""

    claim_id: str
    issue: str
    severity: Literal["error", "warning"]


@dataclass
class CandidateValidationIssue:
    """A single validation issue found in a candidate."""

    candidate_id: str
    issue: str
    severity: Literal["error", "warning"]


@dataclass
class PaperValidationReport:
    """Aggregate validation report for Paper Intelligence artifacts."""

    valid: bool = True
    claim_issues: list[ClaimValidationIssue] = field(default_factory=list)
    candidate_issues: list[CandidateValidationIssue] = field(default_factory=list)
    parser_issues: list[str] = field(default_factory=list)
    schema_issues: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return (
            sum(1 for i in self.claim_issues if i.severity == "error")
            + sum(1 for i in self.candidate_issues if i.severity == "error")
        )


def validate_claim(claim: PaperClaim) -> list[ClaimValidationIssue]:
    """Validate a single PaperClaim against the contract rules.

    - confirmed claims must have evidence_ids
    - paper body facts must not depend solely on web evidence (checked upstream)
    - inferred claims must have rationale_summary
    - conflicting claims must have at least two evidence_ids
    - unknown claims must not invent evidence
    """
    issues: list[ClaimValidationIssue] = []

    # confirmed → must have evidence
    if claim.status == "confirmed" and not claim.evidence_ids:
        issues.append(
            ClaimValidationIssue(
                claim_id=claim.claim_id,
                issue="confirmed claim must have at least one evidence_id",
                severity="error",
            )
        )

    # inferred → must have rationale and not use verified/final wording
    if claim.status == "inferred":
        if not claim.rationale_summary:
            issues.append(
                ClaimValidationIssue(
                    claim_id=claim.claim_id,
                    issue="inferred claim must have rationale_summary",
                    severity="error",
                )
            )

    # conflicting → must have at least two evidence_ids
    if claim.status == "conflicting" and len(claim.evidence_ids) < 2:
        issues.append(
            ClaimValidationIssue(
                claim_id=claim.claim_id,
                issue="conflicting claim must have at least two evidence_ids",
                severity="error",
            )
        )

    # unknown → must not have fabricated evidence
    if claim.status == "unknown" and claim.evidence_ids:
        issues.append(
            ClaimValidationIssue(
                claim_id=claim.claim_id,
                issue="unknown claim should not have evidence_ids (may indicate fabricated evidence)",
                severity="warning",
            )
        )

    return issues


def validate_candidate(candidate: PaperMentionedCandidate) -> list[CandidateValidationIssue]:
    """Validate a PaperMentionedCandidate against contract rules.

    - selection_status must be "paper_mentioned" (never "selected")
    - candidate roles must be consistent with kind
    """
    issues: list[CandidateValidationIssue] = []

    if candidate.selection_status != "paper_mentioned":
        issues.append(
            CandidateValidationIssue(
                candidate_id=candidate.candidate_id,
                issue=f"selection_status must be 'paper_mentioned', got {candidate.selection_status!r}",
                severity="error",
            )
        )

    # compared_baseline should not be confused with proposed_method
    if candidate.kind == "baseline" and candidate.mention_role == "proposed_method":
        issues.append(
            CandidateValidationIssue(
                candidate_id=candidate.candidate_id,
                issue="baseline kind with mention_role 'proposed_method' is contradictory",
                severity="warning",
            )
        )

    return issues


def validate_candidate_not_selected(candidate: PaperMentionedCandidate) -> bool:
    """Verify that a candidate has not been silently marked as selected."""
    return candidate.selection_status == "paper_mentioned"


def validate_page_index(physical_page_index: int, max_page: int) -> bool:
    """Verify a 0-based page index is within valid range."""
    return 0 <= physical_page_index < max_page
