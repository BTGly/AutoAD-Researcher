"""C14-C15: Risk models — aggregation and acceptance.

compute_variant_risk → deterministic risk calculation.
RiskRecord → records all identified risks.
AcceptedRisk → user-accepted medium/high risks.
VariantRiskReport → per-variant risk rollup.
"""

from autoad_researcher.schemas.baseline_architecture import ModificationHook
from autoad_researcher.schemas.transfer_design import (
    AcceptedRisk,
    DimensionJudgment,
    ImplementationVariant,
    RiskRecord,
    VariantRiskReport,
    compute_variant_risk,
)


def build_variant_risk_report(
    variant: ImplementationVariant,
    judgments: list[DimensionJudgment],
    hooks: dict[str, ModificationHook],
    accepted_risks: list[AcceptedRisk] | None = None,
) -> VariantRiskReport:
    """Build a risk report for one variant.

    Computes risk_level via aggregation, builds RiskRecords from judgments.
    """
    # Build RiskRecords from dimension judgments
    records: list[RiskRecord] = []
    for j in judgments:
        if j.risk:
            records.append(RiskRecord(
                risk_id=f"{variant.variant_id}_risk_{j.dimension.value}",
                variant_id=variant.variant_id,
                dimension=j.dimension,
                description=f"Risk from dimension {j.dimension.value}: {j.reasoning[:200]}",
                severity=j.risk,
                evidence_ids=j.repository_evidence_ids + j.paper_evidence_ids,
            ))

    # Add hook-based risks
    for b in variant.hook_bindings:
        hook = hooks.get(b.hook_id)
        if hook is None:
            continue
        if hook.path_classification == "protected_candidate":
            records.append(RiskRecord(
                risk_id=f"{variant.variant_id}_risk_protected_hook",
                variant_id=variant.variant_id,
                dimension=j.dimension if records else ("semantic"),
                description=f"Hook {b.hook_id} is classified as protected_candidate: {', '.join(hook.protected_reasons)}",
                severity="high",
                evidence_ids=hook.evidence_ids,
            ))

    computed = compute_variant_risk(variant, judgments, hooks)

    return VariantRiskReport(
        variant_id=variant.variant_id,
        computed_risk_level=computed,
        records=records,
        accepted_risks=accepted_risks or [],
    )


def accept_risk(
    risk_record: RiskRecord,
    user_evidence_id: str,
) -> AcceptedRisk:
    """Create an AcceptedRisk from a RiskRecord with user evidence."""
    from datetime import datetime, timezone

    return AcceptedRisk(
        risk_id=risk_record.risk_id,
        variant_id=risk_record.variant_id,
        severity=risk_record.severity,
        accepted_by_user=True,
        user_decision_evidence_id=user_evidence_id,
        accepted_at=datetime.now(timezone.utc),
    )
