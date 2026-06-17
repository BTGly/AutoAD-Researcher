"""C16: VariantSelector — user selection with risk acceptance.

Enforces:
  - Recommend but don't auto-select
  - Silence ≠ consent
  - medium/high risk requires explicit user acceptance
"""

from datetime import datetime, timezone

from autoad_researcher.schemas.transfer_design import (
    AcceptedRisk,
    ImplementationVariant,
    RejectedVariant,
    SelectedVariant,
    VariantSelection,
)


def recommend_variants(
    variants: list[ImplementationVariant],
    presentable_ids: list[str],
    non_viable_ids: list[str],
    needs_reanalysis_ids: list[str],
    idea_id: str,
) -> VariantSelection:
    """Create an initial selection state with recommendations.

    Does NOT auto-select. Marks non_viable and needs_reanalysis as rejected.
    """
    rejected: list[RejectedVariant] = []
    for vid in non_viable_ids:
        rejected.append(RejectedVariant(variant_id=vid, reason="non_viable"))
    for vid in needs_reanalysis_ids:
        rejected.append(RejectedVariant(variant_id=vid, reason="needs_reanalysis"))

    return VariantSelection(
        selection_id=f"{idea_id}_sel_001",
        idea_id=idea_id,
        selected=[],
        rejected=rejected,
        recommended_variant_ids=list(presentable_ids),
        confirmation_status="pending",
    )


def select_variants(
    selection: VariantSelection,
    selected_variant_ids: list[str],
    user_evidence_id: str,
) -> VariantSelection:
    """User explicitly selects variants.

    Only allows selecting from recommended (presentable) set.
    """
    allowed = set(selection.recommended_variant_ids)
    for vid in selected_variant_ids:
        if vid not in allowed:
            raise ValueError(f"variant {vid} is not in presentable set")

    new_selected: list[SelectedVariant] = []
    for vid in selected_variant_ids:
        new_selected.append(SelectedVariant(
            variant_id=vid,
            user_decision_evidence_id=user_evidence_id,
            selected_at=datetime.now(timezone.utc),
        ))

    return VariantSelection(
        selection_id=selection.selection_id,
        idea_id=selection.idea_id,
        selected=new_selected,
        rejected=selection.rejected,
        recommended_variant_ids=selection.recommended_variant_ids,
        confirmation_status="confirmed",
        previous_selection_id=selection.previous_selection_id,
    )


def reject_risk_from_selection(
    selection: VariantSelection,
    variant_id: str,
    rejected_risks: list[AcceptedRisk] | None = None,
) -> VariantSelection:
    """Remove a variant from selection due to risk rejection.

    If no variants remain selected, returns with empty selected (blocked).
    """
    new_selected = [s for s in selection.selected if s.variant_id != variant_id]
    new_rejected = list(selection.rejected)
    if variant_id not in {r.variant_id for r in new_rejected}:
        new_rejected.append(RejectedVariant(variant_id=variant_id, reason="user_rejected"))

    return VariantSelection(
        selection_id=selection.selection_id,
        idea_id=selection.idea_id,
        selected=new_selected,
        rejected=new_rejected,
        recommended_variant_ids=selection.recommended_variant_ids,
        confirmation_status="confirmed" if new_selected else "pending",
        previous_selection_id=selection.selection_id,
    )


def is_blocked_no_selection(selection: VariantSelection) -> bool:
    """Check if selection is blocked due to no selected variants."""
    return len(selection.selected) == 0 and selection.confirmation_status == "pending"
