"""C11: VariantGenerator.

Generates 1-3 architectural-level variants from alignment results.
"""

from autoad_researcher.schemas.transfer_design import (
    IdeaContract,
    ImplementationVariant,
)


def generate_variants(
    idea_contract: IdeaContract,
    valid_hook_ids: list[str],
    max_variants: int = 3,
) -> list[ImplementationVariant]:
    """Generate architectural-level variants for a confirmed idea.

    Each variant uses a different primary hook. This is a deterministic
    skeleton; the full implementation will call an LLM to flesh out the
    variant details (adapter_description, expected_behavior_rationale, etc.).

    Args:
        idea_contract: confirmed idea.
        valid_hook_ids: hook IDs from alignment that are viable.
        max_variants: maximum number of variants (default 3).
    """
    variants: list[ImplementationVariant] = []

    if not valid_hook_ids:
        return variants

    # One variant per available hook (up to max_variants)
    for i, hook_id in enumerate(valid_hook_ids[:max_variants]):
        v = ImplementationVariant(
            variant_id=f"{idea_contract.idea_id}_var_{chr(ord('A') + i)}",
            variant_label=f"Variant {chr(ord('A') + i)}: hook {hook_id}",
            idea_id=idea_contract.idea_id,
            primary_hook_id=hook_id,
            risk_level="medium",
            fallback_behavior="Revert to original baseline configuration.",
            expected_behavior_rationale=(
                f"Variant {chr(ord('A') + i)} applies the idea mechanism "
                f"at hook '{hook_id}'. Full rationale will be filled by LLM."
            ),
            idea_contract_evidence_ids=_extract_evidence_ids(idea_contract),
        )
        variants.append(v)

    return variants


def _extract_evidence_ids(idea_contract: IdeaContract) -> list[str]:
    """Extract evidence IDs from IdeaContract."""
    from autoad_researcher.schemas.transfer_design import PaperGroundedIdeaContract, UserProvidedIdeaContract

    source = idea_contract.idea_source
    if isinstance(source, UserProvidedIdeaContract):
        return source.user_evidence_ids
    elif isinstance(source, PaperGroundedIdeaContract):
        return source.paper_evidence_ids
    return []
