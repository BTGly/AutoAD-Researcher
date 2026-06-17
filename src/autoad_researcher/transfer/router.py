"""C8: Idea source routing (path A) and resolution (path B).

IdeaSourceRouter: validates user-provided or paper-grounded idea.
IdeaSourceResolver: presents paper_idea_sources candidates to user.
"""

from autoad_researcher.schemas.transfer_design import IdeaContract, PaperGroundedIdeaContract, UserProvidedIdeaContract


class RoutingResult:
    """Output of IdeaSourceRouter or IdeaSourceResolver."""

    def __init__(
        self,
        route: str,
        idea_source: UserProvidedIdeaContract | PaperGroundedIdeaContract | None = None,
        candidates: list[dict] | None = None,
        needs_reanalysis: str | None = None,
        blocked: bool = False,
        blocked_reason: str | None = None,
    ):
        self.route = route
        self.idea_source = idea_source
        self.candidates = candidates or []
        self.needs_reanalysis = needs_reanalysis
        self.blocked = blocked
        self.blocked_reason = blocked_reason


def route_user_idea(
    user_label: str,
    paper_idea_sources: list[dict],
    user_evidence_id: str,
) -> RoutingResult:
    """Path A: route a user-specified idea.

    Args:
        user_label: what the user said they want to migrate.
        paper_idea_sources: 3.2-exported paper_idea_sources list.
        user_evidence_id: UserInputEvidenceRef id for this input.
    """
    exact_match = None
    for src in paper_idea_sources:
        src_label = src.get("label", "") or src.get("mechanism_label", "") or src.get("title", "")
        if user_label.lower() == src_label.lower():
            exact_match = src
            break

    if exact_match is not None:
        return RoutingResult(
            route="A_paper_grounded",
            idea_source=PaperGroundedIdeaContract(
                paper_idea_source_id=exact_match.get("source_id", exact_match.get("id", "")),
                paper_mechanism_summary=exact_match.get("mechanism_summary", exact_match.get("summary", "")),
                paper_evidence_ids=exact_match.get("evidence_ids", []),
                original_mechanism_rationale=_make_derived_claim(
                    exact_match.get("mechanism_why", exact_match.get("rationale", "")),
                    exact_match.get("evidence_ids", []),
                ),
                transfer_relevance=_make_derived_claim(
                    f"User explicitly selected '{user_label}' as the target mechanism for transfer.",
                    [user_evidence_id],
                ),
            ),
        )

    # Fuzzy match check
    for src in paper_idea_sources:
        src_label = src.get("label", "") or src.get("mechanism_label", "") or src.get("title", "")
        if _fuzzy_match(user_label, src_label):
            # Potential typo, need user confirmation
            return RoutingResult(
                route="A_fuzzy_match_needs_confirmation",
                blocked=True,
                blocked_reason=f"'{user_label}' not found. Did you mean '{src_label}'?",
            )

    # Not in paper at all — could be user's own idea or missed by 3.2
    return RoutingResult(
        route="A_not_found",
        blocked=True,
        blocked_reason=f"'{user_label}' not found in paper_idea_sources. "
                       "Re-route to paper_reanalysis or confirm as user-provided.",
        needs_reanalysis="paper",
    )


def route_user_original_idea(
    user_description: str,
    user_evidence_id: str,
) -> RoutingResult:
    """Path A confirm: user declares a truly original idea (not in paper)."""
    return RoutingResult(
        route="A_user_provided",
        idea_source=UserProvidedIdeaContract(
            user_description=user_description,
            user_evidence_ids=[user_evidence_id],
            mechanism_hypothesis=_make_derived_claim(
                f"User proposed idea: {user_description[:200]}",
                [user_evidence_id],
            ),
            transfer_relevance=_make_derived_claim(
                f"User believes this idea is transferable: {user_description[:200]}",
                [user_evidence_id],
            ),
        ),
    )


def resolve_paper_candidates(
    paper_idea_sources: list[dict],
    baseline_contract_hooks: list[str] | None = None,
) -> RoutingResult:
    """Path B: present paper_idea_sources as candidates.

    Args:
        paper_idea_sources: 3.2-exported candidates.
        baseline_contract_hooks: optional hook_name list for hint mapping.
    """
    if not paper_idea_sources:
        return RoutingResult(
            route="B_no_candidates",
            blocked=True,
            blocked_reason="No paper_idea_sources available. Cannot present candidates.",
        )

    candidates = []
    for src in paper_idea_sources:
        candidate = {
            "source_id": src.get("source_id", src.get("id", "")),
            "label": src.get("label", src.get("mechanism_label", src.get("title", ""))),
            "mechanism_summary": src.get("mechanism_summary", src.get("summary", "")),
            "paper_location": src.get("paper_location", src.get("location", "")),
            "role_in_paper": src.get("role_in_paper", src.get("role", "unknown")),
            "evidence_ids": src.get("evidence_ids", []),
            "possible_hooks": _map_hooks(src, baseline_contract_hooks),
        }
        candidates.append(candidate)

    return RoutingResult(
        route="B_candidates_ready",
        candidates=candidates,
    )


def _map_hooks(src: dict, hooks: list[str] | None) -> list[str]:
    """Map paper idea source to possible baseline hooks (inferred, not fact)."""
    if not hooks:
        return []
    description = (src.get("mechanism_summary", "") + " " +
                   src.get("summary", "")).lower()
    possible = []
    for h in hooks:
        h_lower = h.lower()
        if "backbone" in description and "backbone" in h_lower:
            possible.append(h)
        elif "memory" in description and "memory" in h_lower:
            possible.append(h)
        elif "embedding" in description and "embedding" in h_lower:
            possible.append(h)
        elif "distance" in description and "distance" in h_lower:
            possible.append(h)
        elif "anomaly" in description and ("anomaly" in h_lower or "score" in h_lower):
            possible.append(h)
    return possible


def _fuzzy_match(a: str, b: str, threshold: float = 0.7) -> bool:
    """Simple fuzzy match based on token overlap."""
    a_tokens = set(a.lower().split())
    b_tokens = set(b.lower().split())
    if not a_tokens or not b_tokens:
        return False
    intersection = a_tokens & b_tokens
    return len(intersection) / max(len(a_tokens), len(b_tokens)) >= threshold


def _make_derived_claim(value: str, evidence_ids: list[str]):
    """Create a DerivedClaim — imported lazily to avoid circular imports."""
    from autoad_researcher.schemas.transfer_design import DerivedClaim
    safe_value = value if value and value.strip() else "(no value provided)"
    return DerivedClaim(
        value=safe_value,
        supporting_evidence_ids=evidence_ids,
    )
