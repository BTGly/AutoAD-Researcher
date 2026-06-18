"""Step 3.9: Scientific conclusion derivation — sealed implementation.

Matches the sealed contract in docs/3.9开发计划.md v2.12.
Uses ScientificConclusion enum from schemas/experiment_planning.py and
IdeaSupportConclusion from schemas/results_analysis.py.
"""

from collections.abc import Sequence

from autoad_researcher.schemas.experiment_planning import ScientificConclusion
from autoad_researcher.schemas.results_analysis import (
    IdeaSupportConclusion,
    VariantScientificConclusion,
)


def _make_incomplete_conclusion(
    variant_id: str,
    protocol_seeds: Sequence[int],
) -> VariantScientificConclusion:
    return VariantScientificConclusion(
        variant_id=variant_id,
        conclusion=ScientificConclusion.INCOMPLETE,
        matched_rule_id="missing_variant",
        completed_seed_pairs=[],
        missing_seed_pairs=list(protocol_seeds),
        evidence_refs=[],
    )


def derive_idea_support(
    conclusions: Sequence[VariantScientificConclusion],
    selected_variant_ids: Sequence[str],
    multiple_variant_policy: str,
    protocol_seeds: Sequence[int],
) -> IdeaSupportConclusion:
    """Derive the idea-level support conclusion from per-variant conclusions.

    Operates on the preregistered statistical decision rules encoded in
    ScientificConclusion per variant. Does NOT use heuristic delta-sign
    comparisons — the conclusion values come from the full StatisticalAnalysisPlan
    pipeline, not from raw metric sign.
    """
    selected = list(selected_variant_ids)

    if not selected:
        return IdeaSupportConclusion.CANNOT_JUDGE

    if len(selected) != len(set(selected)):
        raise ValueError("duplicate selected_variant_ids")

    by_variant: dict[str, VariantScientificConclusion] = {}
    for c in conclusions:
        if c.variant_id in by_variant:
            raise ValueError(f"duplicate conclusion for variant '{c.variant_id}'")
        if c.variant_id not in selected:
            raise ValueError(f"conclusion for unselected variant '{c.variant_id}'")
        by_variant[c.variant_id] = c

    normalized = [
        by_variant.get(vid, _make_incomplete_conclusion(vid, protocol_seeds))
        for vid in selected
    ]

    statuses = [c.conclusion for c in normalized]
    has_beneficial = ScientificConclusion.BENEFICIAL in statuses
    has_worse = ScientificConclusion.WORSE in statuses
    has_equivalent = ScientificConclusion.PRACTICALLY_EQUIVALENT in statuses
    has_mixed = ScientificConclusion.MIXED in statuses
    all_beneficial = all(s == ScientificConclusion.BENEFICIAL for s in statuses)
    all_worse = all(s == ScientificConclusion.WORSE for s in statuses)
    all_incomplete = all(s == ScientificConclusion.INCOMPLETE for s in statuses)

    if multiple_variant_policy == "descriptive_only" and len(normalized) > 1:
        return IdeaSupportConclusion.MULTIPLE_VARIANTS_DESCRIPTIVE

    if has_beneficial and has_worse:
        return IdeaSupportConclusion.IMPLEMENTATION_SENSITIVE
    if all_beneficial:
        return IdeaSupportConclusion.CONSISTENTLY_SUPPORTED
    if has_beneficial and not has_worse:
        return IdeaSupportConclusion.SUPPORTED_BY_AT_LEAST_ONE
    if all_worse:
        return IdeaSupportConclusion.NOT_SUPPORTED_BY_TESTED
    if all_incomplete:
        return IdeaSupportConclusion.CANNOT_JUDGE
    if has_worse and not has_beneficial and (has_equivalent or has_mixed):
        return IdeaSupportConclusion.NOT_SUPPORTED_OR_NOT_DEMONSTRATED
    if not has_beneficial and not has_worse and (has_equivalent or has_mixed):
        return IdeaSupportConclusion.NOT_DEMONSTRATED_OR_PARTIAL
    return IdeaSupportConclusion.CANNOT_JUDGE
