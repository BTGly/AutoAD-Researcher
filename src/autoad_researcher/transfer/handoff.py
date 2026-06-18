"""C24: Handoff emitter — builds IdeaTransferDesignHandoff for Step 3.5."""

import hashlib
import json

from autoad_researcher.schemas.transfer_design import (
    IdeaContract,
    IdeaTransferAnalysis,
    IdeaTransferDesignHandoff,
    ImplementationVariant,
    TransferConstraint,
    UnresolvedDimension,
    VariantRiskReport,
)
from autoad_researcher.transfer.validator import classify_unresolved


def build_handoff(
    run_id: str,
    source_context_id: str,
    source_context_version: int,
    source_context_sha256: str,
    idea_contract: IdeaContract,
    transfer_analysis: IdeaTransferAnalysis,
    transfer_constraints: list[TransferConstraint],
    selected_variants: list[ImplementationVariant],
    risk_reports: list[VariantRiskReport],
    unresolved_dimensions: list[UnresolvedDimension],
    validator_report_sha256: str,
) -> IdeaTransferDesignHandoff:
    """Build the 3.4 → 3.5 handoff.

    Filters out design_blocking unresolved dimensions before handoff.
    """
    design_blocking, experiment_resolvable, nonblocking, _needs_reanalysis = classify_unresolved(unresolved_dimensions)

    if design_blocking:
        raise ValueError(
            f"design_blocking unresolved dimensions cannot enter handoff: "
            f"{[u.dimension.value for u in design_blocking]}"
        )

    idea_sha = _sha256(json.dumps(idea_contract.model_dump(), sort_keys=True, default=str))
    selection_sha = _sha256(
        json.dumps([v.model_dump() for v in selected_variants], sort_keys=True, default=str)
    )

    return IdeaTransferDesignHandoff(
        run_id=run_id,
        source_context_id=source_context_id,
        source_context_version=source_context_version,
        source_context_sha256=source_context_sha256,
        confirmed_idea=idea_contract,
        idea_contract_sha256=idea_sha,
        transfer_analysis=transfer_analysis,
        transfer_constraints=transfer_constraints,
        selected_variants=selected_variants,
        variant_selection_sha256=selection_sha,
        variant_risk_reports=risk_reports,
        experiment_resolvable_dimensions=experiment_resolvable,
        nonblocking_warnings=nonblocking,
        validator_report_sha256=validator_report_sha256,
    )


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()
