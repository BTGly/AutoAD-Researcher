"""C12: CompatibilityAnalyzer — per-variant deterministic analysis."""

from autoad_researcher.schemas.transfer_design import (
    CompatibilityDimension,
    CompatibilityStatus,
    DimensionJudgment,
    ImplementationVariant,
    IdeaTransferAnalysis,
    TransferConstraint,
    TransferStatus,
    VariantTransferAnalysis,
    derive_variant_status,
)


def analyze_variant(
    variant: ImplementationVariant,
    constraints: list[TransferConstraint],
    default_status: CompatibilityStatus = CompatibilityStatus.COMPATIBLE,
) -> VariantTransferAnalysis:
    """Analyze one variant across all 9 compatibility dimensions.

    This skeleton marks all dimensions as the default_status. The full
    implementation will call an LLM to produce per-dimension judgments.
    """
    judgments: list[DimensionJudgment] = []
    for dim in CompatibilityDimension:
        j = DimensionJudgment(
            variant_id=variant.variant_id,
            dimension=dim,
            status=default_status,
            blocking=(default_status == CompatibilityStatus.INCOMPATIBLE),
            reasoning=f"Skeleton: dimension {dim.value} marked as {default_status.value}.",
        )
        judgments.append(j)

    overall = derive_variant_status(judgments, constraints)

    return VariantTransferAnalysis(
        variant_id=variant.variant_id,
        dimensions=judgments,
        overall_status=overall,
        constraints=constraints,
    )


def analyze_all_variants(
    variants: list[ImplementationVariant],
    constraints: list[TransferConstraint],
) -> IdeaTransferAnalysis:
    """Run compatibility analysis on all variants for one idea."""
    idea_id = variants[0].idea_id if variants else ""
    analyses: dict[str, VariantTransferAnalysis] = {}
    viable: list[str] = []
    conditional: list[str] = []
    non_viable: list[str] = []
    needs_reanalysis: list[str] = []

    for v in variants:
        va = analyze_variant(v, constraints)
        analyses[v.variant_id] = va
        if va.overall_status == TransferStatus.VIABLE:
            viable.append(v.variant_id)
        elif va.overall_status == TransferStatus.VIABLE_WITH_CONDITIONS:
            conditional.append(v.variant_id)
        elif va.overall_status == TransferStatus.NON_VIABLE:
            non_viable.append(v.variant_id)
        elif va.overall_status == TransferStatus.NEEDS_REANALYSIS:
            needs_reanalysis.append(v.variant_id)

    return IdeaTransferAnalysis(
        idea_id=idea_id,
        variant_analyses=analyses,
        viable_variant_ids=viable,
        conditional_variant_ids=conditional,
        non_viable_variant_ids=non_viable,
        needs_reanalysis_variant_ids=needs_reanalysis,
    )


def filter_variants(
    analysis: IdeaTransferAnalysis,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Filter variants into presentable / non-viable / needs_reanalysis.

    Returns (presentable_ids, non_viable_ids, needs_reanalysis_ids, all_need_reanalysis).
    """
    presentable = analysis.viable_variant_ids + analysis.conditional_variant_ids
    non_viable = analysis.non_viable_variant_ids
    needs_re = analysis.needs_reanalysis_variant_ids
    all_need_reanalysis = len(presentable) == 0 and len(needs_re) > 0

    return presentable, non_viable, needs_re, all_need_reanalysis
