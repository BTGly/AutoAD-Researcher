"""C9: IdeaContractNormalizer.

Converts a routed idea source into a formal IdeaContract.
"""

from datetime import datetime, timezone

from autoad_researcher.schemas.transfer_design import (
    IdeaContract,
    PaperGroundedIdeaContract,
    UserProvidedIdeaContract,
)


def normalize_idea_contract(
    idea_id: str,
    idea_source: UserProvidedIdeaContract | PaperGroundedIdeaContract,
    confirmed_by_user: bool = False,
    confirmation_evidence_id: str | None = None,
) -> IdeaContract:
    """Normalize an IdeaContract from a raw idea source.

    Args:
        idea_id: unique idea identifier.
        idea_source: the discriminated union source.
        confirmed_by_user: whether user has confirmed.
        confirmation_evidence_id: UserInputEvidenceRef for the confirmation.
    """
    status = "confirmed" if confirmed_by_user else "pending"

    return IdeaContract(
        idea_id=idea_id,
        idea_source=idea_source,
        confirmation_status=status,
        confirmed_by_user_at=datetime.now(timezone.utc) if confirmed_by_user else None,
        confirmation_evidence_id=confirmation_evidence_id,
    )


def confirm_idea_contract(
    contract: IdeaContract,
    confirmation_evidence_id: str,
) -> IdeaContract:
    """Confirm a pending IdeaContract with user evidence."""
    return IdeaContract(
        idea_id=contract.idea_id,
        idea_source=contract.idea_source,
        must_preserve_behaviors=contract.must_preserve_behaviors,
        confirmation_status="confirmed",
        confirmed_by_user_at=datetime.now(timezone.utc),
        confirmation_evidence_id=confirmation_evidence_id,
        supersedes_idea_id=contract.supersedes_idea_id,
    )
