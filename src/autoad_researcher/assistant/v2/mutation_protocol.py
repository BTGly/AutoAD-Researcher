"""Hash-bound, field-level mutations for the durable research draft."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from autoad_researcher.assistant.v2.contract_hashing import confirmation_draft_sha256
from autoad_researcher.assistant.v2.intent_contract import (
    CONTRACT_DRAFT_FILE,
    ResearchIntentContract,
    load_contract_draft,
    refresh_contract_state,
)
from autoad_researcher.core.control_plane.io import atomic_write_json
from autoad_researcher.core.control_plane.unit_of_work import ControlPlaneUnitOfWork


MutationOperation = Literal["set", "replace", "remove"]
MutationStatus = Literal["applied", "unchanged", "rejected"]
MutationReason = Literal[
    "applied",
    "no_operations",
    "draft_hash_mismatch",
    "missing_full_turn_evidence",
    "invalid_evidence_span",
    "unsupported_target",
    "set_requires_empty_target",
    "invalid_remove_value",
    "contract_schema_rejected",
]


INTENT_MUTATION_TARGETS = frozenset({
    "research_goal",
    "research_object",
    "target_platform",
    "workload",
    "baseline",
    "dataset",
    "evaluation_protocol",
    "primary_metrics",
    "secondary_metrics",
    "metric_priority",
    "success_criteria",
    "compute_environment",
    "execution_mode",
    "user_improvement_hints",
    "user_target_module_hints",
    "preferred_method_hints",
    "risk_preference",
    "allowed_change_scope",
})

_REMOVE_VALUES: dict[str, Any] = {
    "research_goal": None,
    "research_object": None,
    "target_platform": None,
    "workload": None,
    "baseline": None,
    "dataset": None,
    "evaluation_protocol": None,
    "primary_metrics": [],
    "secondary_metrics": [],
    "metric_priority": None,
    "success_criteria": None,
    "compute_environment": {},
    "execution_mode": "plan_only",
    "user_improvement_hints": [],
    "user_target_module_hints": [],
    "preferred_method_hints": [],
    "risk_preference": None,
    "allowed_change_scope": [],
}


class EvidenceSpan(BaseModel):
    """An exact character span in the current user turn."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["current_user_turn"] = "current_user_turn"
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered_span(self) -> "EvidenceSpan":
        if self.end <= self.start:
            raise ValueError("evidence span end must be greater than start")
        return self


class FieldMutation(BaseModel):
    """One proposed change to a user-owned intent field."""

    model_config = ConfigDict(extra="forbid")

    operation: MutationOperation
    target: str = Field(min_length=1)
    proposed_value: Any | None = None
    evidence_spans: list[EvidenceSpan] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ContractMutationProposal(BaseModel):
    """One all-or-nothing mutation proposal for the persisted draft."""

    model_config = ConfigDict(extra="forbid")

    base_draft_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    full_turn_mutation_evidence: str = Field(min_length=1)
    operations: list[FieldMutation] = Field(default_factory=list)


class MutationReceipt(BaseModel):
    """Authoritative result used by reply and confirmation layers."""

    model_config = ConfigDict(extra="forbid")

    status: MutationStatus
    reason: MutationReason
    before_draft_sha256: str | None = None
    after_draft_sha256: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    contract: ResearchIntentContract | None = None


def apply_contract_mutation(
    run_dir: Path,
    *,
    user_input: str,
    proposal: ContractMutationProposal,
) -> MutationReceipt:
    """Validate and atomically publish all operations, or publish none."""

    if proposal.full_turn_mutation_evidence.strip() != user_input.strip():
        return _rejected("missing_full_turn_evidence")
    if not proposal.operations:
        existing = load_contract_draft(run_dir)
        current_hash = confirmation_draft_sha256(existing) if existing is not None else None
        return MutationReceipt(
            status="unchanged",
            reason="no_operations",
            before_draft_sha256=current_hash,
            after_draft_sha256=current_hash,
            contract=existing,
        )
    for operation in proposal.operations:
        if operation.target not in INTENT_MUTATION_TARGETS:
            return _rejected("unsupported_target")
        if not all(_span_matches(span, user_input) for span in operation.evidence_spans):
            return _rejected("invalid_evidence_span")
        if operation.operation == "remove" and operation.proposed_value is not None:
            return _rejected("invalid_remove_value")

    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {run_dir}")
    with ControlPlaneUnitOfWork(run_dir):
        existing = load_contract_draft(run_dir)
        before_hash = confirmation_draft_sha256(existing) if existing is not None else None
        if proposal.base_draft_sha256 != before_hash:
            return _rejected(
                "draft_hash_mismatch",
                before_hash=before_hash,
                contract=existing,
            )
        draft = existing.model_copy(deep=True) if existing is not None else ResearchIntentContract(run_id=run_dir.name)
        values = draft.model_dump(mode="python")
        changed_fields: list[str] = []
        for operation in proposal.operations:
            current = values[operation.target]
            if operation.operation == "set" and current not in (None, "", [], {}):
                return _rejected(
                    "set_requires_empty_target",
                    before_hash=before_hash,
                    contract=existing,
                )
            next_value = (
                _REMOVE_VALUES[operation.target]
                if operation.operation == "remove"
                else operation.proposed_value
            )
            if current != next_value:
                values[operation.target] = next_value
                if operation.target not in changed_fields:
                    changed_fields.append(operation.target)
        if not changed_fields:
            return MutationReceipt(
                status="unchanged",
                reason="no_operations",
                before_draft_sha256=before_hash,
                after_draft_sha256=before_hash,
                contract=draft,
            )
        try:
            updated = ResearchIntentContract.model_validate(values)
        except ValidationError:
            return _rejected(
                "contract_schema_rejected",
                before_hash=before_hash,
                contract=existing,
            )
        refresh_contract_state(updated)
        after_hash = confirmation_draft_sha256(updated)
        atomic_write_json(run_dir / CONTRACT_DRAFT_FILE, updated.model_dump(mode="json"))

    receipt = MutationReceipt(
        status="applied",
        reason="applied",
        before_draft_sha256=before_hash,
        after_draft_sha256=after_hash,
        changed_fields=changed_fields,
        contract=updated,
    )
    _append_receipt_event(run_dir, receipt)
    return receipt


def _span_matches(span: EvidenceSpan, user_input: str) -> bool:
    return span.end <= len(user_input) and user_input[span.start:span.end] == span.text


def _rejected(
    reason: MutationReason,
    *,
    before_hash: str | None = None,
    contract: ResearchIntentContract | None = None,
) -> MutationReceipt:
    return MutationReceipt(
        status="rejected",
        reason=reason,
        before_draft_sha256=before_hash,
        after_draft_sha256=before_hash,
        contract=contract,
    )


def _append_receipt_event(run_dir: Path, receipt: MutationReceipt) -> None:
    from autoad_researcher.assistant.v2.event_service import append_typed_event

    append_typed_event(
        run_dir,
        "contract.mutation.applied",
        receipt.model_dump(mode="json", exclude={"contract"}),
    )
