"""Bounded paper evidence repair loop (P9).

Repairs missing evidence, wrong page indices, and incorrect candidate roles
within strict budget limits. Cannot consume analysis budget or expand permissions.
"""

from pathlib import Path
from typing import Literal

from autoad_researcher.paper_intelligence.errors import PaperRepairExhaustedError
from autoad_researcher.paper_intelligence.models import (
    PaperAgentBudget,
    PaperClaim,
    PaperMentionedCandidate,
)
from autoad_researcher.paper_intelligence.validator import (
    ClaimValidationIssue,
    CandidateValidationIssue,
    PaperValidationReport,
)


def repair_can_proceed(
    budget: PaperAgentBudget,
    repairs_remaining: int,
    repair_llm_calls_used: int,
) -> bool:
    """Check whether repair can proceed under budget constraints."""
    if repairs_remaining < 1:
        return False
    if repair_llm_calls_used >= budget.max_repair_llm_calls:
        return False
    return True


def repair_claim(
    claim: PaperClaim,
    issue: ClaimValidationIssue,
) -> PaperClaim | None:
    """Attempt to repair a single claim validation issue.

    Only handles repairable issues:
    - confirmed without evidence → downgrade to unknown
    - inferred without rationale → add placeholder rationale

    Returns the repaired claim or None if not repairable.
    """
    if issue.issue.startswith("confirmed claim must have at least one evidence_id"):
        return PaperClaim(
            claim_id=claim.claim_id,
            subject=claim.subject,
            predicate=claim.predicate,
            value=claim.value,
            status="unknown",
            confidence="low",
            rationale_summary="downgraded: missing evidence after repair",
        )

    if issue.issue.startswith("inferred claim must have rationale_summary"):
        return PaperClaim(
            claim_id=claim.claim_id,
            subject=claim.subject,
            predicate=claim.predicate,
            value=claim.value,
            status="unknown" if not claim.evidence_ids else "inferred",
            confidence="low",
            evidence_ids=claim.evidence_ids,
            rationale_summary="insufficient evidence after analysis",
        )

    return None


def repair_candidate(
    candidate: PaperMentionedCandidate,
    issue: CandidateValidationIssue,
) -> PaperMentionedCandidate | None:
    """Attempt to repair a single candidate validation issue.

    Only handles: selection_status not paper_mentioned → reset to paper_mentioned.
    """
    if "selection_status must be 'paper_mentioned'" in issue.issue:
        return PaperMentionedCandidate(
            candidate_id=candidate.candidate_id,
            kind=candidate.kind,
            name=candidate.name,
            normalized_name=candidate.normalized_name,
            mention_role=candidate.mention_role,
            selection_status="paper_mentioned",
            evidence_ids=candidate.evidence_ids,
            warnings=[*candidate.warnings, "repaired: selection_status reset to paper_mentioned"],
        )

    return None


def run_paper_repair(
    claims: list[PaperClaim],
    candidates: list[PaperMentionedCandidate],
    validation_report: PaperValidationReport,
    budget: PaperAgentBudget,
) -> tuple[list[PaperClaim], list[PaperMentionedCandidate], int]:
    """Run the bounded paper repair loop.

    Returns (repaired_claims, repaired_candidates, repairs_used).
    Does NOT consume the analysis budget; uses repair reserve only.
    """
    repaired_claims = list(claims)
    repaired_candidates = list(candidates)
    repairs_used = 0
    llm_calls_used = 0

    # Repair claims
    for issue in validation_report.claim_issues:
        if issue.severity != "error":
            continue
        if not repair_can_proceed(budget, budget.max_repairs - repairs_used, llm_calls_used):
            break

        for i, claim in enumerate(repaired_claims):
            if claim.claim_id == issue.claim_id:
                repaired = repair_claim(claim, issue)
                if repaired is not None:
                    repaired_claims[i] = repaired
                    repairs_used += 1
                    llm_calls_used += 1
                break

    # Repair candidates
    for issue in validation_report.candidate_issues:
        if issue.severity != "error":
            continue
        if not repair_can_proceed(budget, budget.max_repairs - repairs_used, llm_calls_used):
            break

        for i, candidate in enumerate(repaired_candidates):
            if candidate.candidate_id == issue.candidate_id:
                repaired = repair_candidate(candidate, issue)
                if repaired is not None:
                    repaired_candidates[i] = repaired
                    repairs_used += 1
                    llm_calls_used += 1
                break

    return repaired_claims, repaired_candidates, repairs_used
