"""Step 3.9: Scientific conclusion derivation from evidence."""

from autoad_researcher.schemas.results_analysis import (
    EvidenceSufficiency,
    IdeaSupportConclusion,
    ResolvedMetricEvidence,
    ResolvedValidityEvidence,
    VariantScientificConclusion,
)


def _make_incomplete_conclusion(
    variant_id: str,
    reason: str,
) -> VariantScientificConclusion:
    """Create an inconclusive conclusion when evidence is insufficient."""
    return VariantScientificConclusion(
        variant_id=variant_id,
        conclusion="inconclusive",
        confidence=0.0,
        supporting_metrics=[],
        contradicting_metrics=[],
        sufficiency=EvidenceSufficiency(
            all_metrics_accounted_for=False,
            all_validity_checks_passed=False,
            sufficient_seeds_available=False,
            sufficiency_summary=reason,
        ),
    )


def _derive_support_from_comparisons(
    evidence: ResolvedMetricEvidence,
) -> tuple[list[str], list[str]]:
    """Derive supporting and contradicting metrics from evidence comparisons."""
    supporting: list[str] = []
    contradicting: list[str] = []

    for comp in evidence.metric_comparisons:
        metric_name = comp.key.metric_name
        if comp.mean_delta is not None:
            if comp.mean_delta > 0:
                supporting.append(metric_name)
            elif comp.mean_delta < 0:
                contradicting.append(metric_name)

    return supporting, contradicting


def derive_idea_support(
    variant_id: str,
    metric_evidence: ResolvedMetricEvidence | None,
    validity_evidence: ResolvedValidityEvidence | None,
    sufficiency: EvidenceSufficiency | None,
) -> VariantScientificConclusion:
    """Derive the scientific conclusion for a variant."""
    if metric_evidence is None:
        return _make_incomplete_conclusion(
            variant_id, "no metric evidence available"
        )
    if not metric_evidence.metric_comparisons:
        return _make_incomplete_conclusion(
            variant_id, "no metric comparisons available"
        )
    if validity_evidence and not validity_evidence.overall_valid:
        return _make_incomplete_conclusion(
            variant_id, "validity checks failed"
        )

    supporting, contradicting = _derive_support_from_comparisons(metric_evidence)

    if supporting and not contradicting:
        conclusion: IdeaSupportConclusion = "supported"
        confidence = 0.9
    elif supporting and contradicting:
        conclusion = "partially_supported"
        confidence = 0.5
    elif not supporting and contradicting:
        conclusion = "not_supported"
        confidence = 0.9
    else:
        conclusion = "inconclusive"
        confidence = 0.0

    return VariantScientificConclusion(
        variant_id=variant_id,
        evidence=metric_evidence,
        sufficiency=sufficiency,
        conclusion=conclusion,
        confidence=confidence,
        supporting_metrics=supporting,
        contradicting_metrics=contradicting,
    )
