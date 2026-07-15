"""Orchestrates semantic interpretation and one atomic intent mutation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from autoad_researcher.assistant.v2.contract_hashing import confirmation_draft_sha256
from autoad_researcher.assistant.v2.intent_contract import (
    DEFAULT_FORBIDDEN_CHANGE_SCOPE,
    ResearchIntentContract,
)
from autoad_researcher.assistant.v2.mutation_protocol import MutationReceipt, apply_contract_mutation
from autoad_researcher.assistant.v2.research_intent_interpreter import (
    ResearchIntentInterpretation,
    interpret_research_intent,
)
from autoad_researcher.assistant.v2.research_semantics import ContractSemanticMetadata


class IntentMutationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt: MutationReceipt
    interpretation: ResearchIntentInterpretation | None = None


def interpret_and_apply_intent_mutation(
    *,
    run_dir: Path,
    user_input: str,
    persisted_contract: ResearchIntentContract | None,
    recent_mutation_receipts: list[dict[str, Any]],
    recent_dialogue: list[dict[str, str]],
    active_sources: list[dict[str, Any]],
    usable_evidence: list[dict[str, Any]],
    unusable_evidence: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    pending_confirmation: dict[str, Any] | None,
    api_key: str,
    provider_url: str,
    model: str,
) -> IntentMutationOutcome:
    interpretation_outcome = interpret_research_intent(
        run_dir=run_dir,
        user_input=user_input,
        persisted_contract=(
            persisted_contract.model_dump(mode="json")
            if persisted_contract is not None
            else None
        ),
        persisted_draft_sha256=(
            confirmation_draft_sha256(persisted_contract)
            if persisted_contract is not None
            else None
        ),
        recent_mutation_receipts=recent_mutation_receipts,
        recent_dialogue=recent_dialogue,
        active_sources=active_sources,
        usable_evidence=usable_evidence,
        unusable_evidence=unusable_evidence,
        jobs=jobs,
        pending_confirmation=pending_confirmation,
        system_safety_policy=(
            list(persisted_contract.system_safety_policy)
            if persisted_contract is not None
            else list(DEFAULT_FORBIDDEN_CHANGE_SCOPE)
        ),
        api_key=api_key,
        provider_url=provider_url,
        model=model,
    )
    if interpretation_outcome.status == "failed":
        current_hash = (
            confirmation_draft_sha256(persisted_contract)
            if persisted_contract is not None
            else None
        )
        return IntentMutationOutcome(receipt=MutationReceipt(
            status="unchanged",
            reason=f"interpreter_{interpretation_outcome.failure_reason}",
            before_draft_sha256=current_hash,
            after_draft_sha256=current_hash,
            contract=persisted_contract,
        ))

    interpretation = interpretation_outcome.interpretation
    if interpretation is None:
        raise RuntimeError("successful interpretation must include an interpretation")
    metadata = ContractSemanticMetadata(
        research_modes=interpretation.research_modes,
        open_questions=interpretation.open_questions,
        evidence_conflicts=interpretation.evidence_conflicts,
    )
    receipt = apply_contract_mutation(
        run_dir,
        user_input=user_input,
        proposal=interpretation.intent_mutation,
        semantic_metadata=metadata,
    )
    return IntentMutationOutcome(receipt=receipt, interpretation=interpretation)
