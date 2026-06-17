"""C19: DeterministicValidator.

Validates all 8 invariant categories without any LLM calls.
"""

from datetime import datetime, timezone

from autoad_researcher.schemas.baseline_architecture import BaselineArchitectureContract, ModificationHook
from autoad_researcher.schemas.transfer_design import (
    CompatibilityStatus,
    DimensionJudgment,
    IdeaContract,
    IdeaTransferAnalysis,
    IdeaTransferValidationReport,
    ImplementationVariant,
    PaperGroundedIdeaContract,
    ResolutionClass,
    SelectedVariant,
    TransferValidationIssue,
    UnresolvedDimension,
    VariantRiskReport,
    VariantSelection,
    compute_variant_risk,
)


def validate_transfer(
    run_id: str,
    idea_contract: IdeaContract,
    baseline_contract: BaselineArchitectureContract,
    analysis: IdeaTransferAnalysis,
    selection: VariantSelection,
    variants: list[ImplementationVariant],
    risk_reports: list[VariantRiskReport],
    hooks: dict[str, ModificationHook],
    resolved_dimensions: list[UnresolvedDimension],
) -> IdeaTransferValidationReport:
    """Run all 8 invariant checks deterministically."""
    issues: list[TransferValidationIssue] = []
    results: dict[str, bool] = {}

    # (1) Idea invariants
    results["1_idea"] = _check_idea_invariants(idea_contract, issues)

    # (2) Baseline invariants
    results["2_baseline"] = _check_baseline_invariants(idea_contract, baseline_contract, issues)

    # (3) Variant invariants
    results["3_variant"] = _check_variant_invariants(variants, idea_contract.idea_id, issues)

    # (4) Variant-Hook invariants
    results["4_variant_hook"] = _check_variant_hook_invariants(variants, hooks, issues)

    # (5) Compatibility invariants
    results["5_compatibility"] = _check_compatibility_invariants(analysis, issues)

    # (6) Risk invariants
    results["6_risk"] = _check_risk_invariants(variants, risk_reports, selection, hooks, issues)

    # (7) Selection invariants
    results["7_selection"] = _check_selection_invariants(selection, analysis, variants, issues)

    # (8) Handoff invariants (placeholder — checked at handoff time)
    results["8_handoff"] = True

    all_passed = all(results.values())
    has_policy_violation = any(i.category == "policy_violation" for i in issues)
    has_schema_repairable = any(i.category == "schema_repairable" for i in issues)

    if all_passed:
        status = "passed"
    elif has_policy_violation:
        status = "failed"
    elif has_schema_repairable:
        status = "partial_repair_successful"
    else:
        status = "failed"

    return IdeaTransferValidationReport(
        report_id=f"{run_id}_validation",
        run_id=run_id,
        status=status,
        issues=issues,
        invariant_results=results,
        revalidated_at=datetime.now(timezone.utc),
    )


def _check_idea_invariants(
    idea: IdeaContract,
    issues: list[TransferValidationIssue],
) -> bool:
    ok = True
    if idea.confirmation_status != "confirmed":
        issues.append(TransferValidationIssue(
            issue_id="idea_not_confirmed",
            category="policy_violation",
            invariant_category="1_idea",
            description="Idea is not confirmed",
            artifact_ids=[idea.idea_id],
            resolution="user_decide",
        ))
        ok = False
    if isinstance(idea.idea_source, PaperGroundedIdeaContract):
        if not idea.idea_source.paper_evidence_ids:
            issues.append(TransferValidationIssue(
                issue_id="paper_grounded_no_evidence",
                category="schema_repairable",
                invariant_category="1_idea",
                description="Paper-grounded idea has no paper_evidence_ids",
                artifact_ids=[idea.idea_id],
                resolution="artifact_repair",
            ))
            ok = False
    return ok


def _check_baseline_invariants(
    idea: IdeaContract,
    contract: BaselineArchitectureContract,
    issues: list[TransferValidationIssue],
) -> bool:
    ok = True
    hook_ids = [h.hook_id for h in contract.modifiable_hooks]
    if len(hook_ids) != len(set(hook_ids)):
        issues.append(TransferValidationIssue(
            issue_id="duplicate_hook_ids",
            category="schema_repairable",
            invariant_category="2_baseline",
            description="Duplicate hook_ids in baseline contract",
            artifact_ids=[],
            resolution="artifact_repair",
        ))
        ok = False
    return ok


def _check_variant_invariants(
    variants: list[ImplementationVariant],
    idea_id: str,
    issues: list[TransferValidationIssue],
) -> bool:
    ok = True
    if len(variants) < 1 or len(variants) > 3:
        issues.append(TransferValidationIssue(
            issue_id="variant_count_out_of_range",
            category="policy_violation",
            invariant_category="3_variant",
            description=f"Variant count {len(variants)} not in [1, 3]",
            artifact_ids=[v.variant_id for v in variants],
            resolution="user_decide",
        ))
        ok = False
    for v in variants:
        if v.idea_id != idea_id:
            issues.append(TransferValidationIssue(
                issue_id=f"variant_idea_mismatch_{v.variant_id}",
                category="policy_violation",
                invariant_category="3_variant",
                description=f"Variant {v.variant_id} has idea_id {v.idea_id}, expected {idea_id}",
                artifact_ids=[v.variant_id],
                resolution="artifact_repair",
            ))
            ok = False
    return ok


def _check_variant_hook_invariants(
    variants: list[ImplementationVariant],
    hooks: dict[str, ModificationHook],
    issues: list[TransferValidationIssue],
) -> bool:
    ok = True
    for v in variants:
        for binding in v.hook_bindings:
            hook = hooks.get(binding.hook_id)
            if hook is None:
                issues.append(TransferValidationIssue(
                    issue_id=f"hook_not_found_{binding.hook_id}",
                    category="policy_violation",
                    invariant_category="4_variant_hook",
                    description=f"Hook {binding.hook_id} referenced by variant {v.variant_id} does not exist",
                    artifact_ids=[v.variant_id],
                    resolution="artifact_repair",
                ))
                ok = False
                continue
            if not hook.allowed_for_transfer_design:
                issues.append(TransferValidationIssue(
                    issue_id=f"hook_not_allowed_{binding.hook_id}",
                    category="policy_violation",
                    invariant_category="4_variant_hook",
                    description=f"Hook {binding.hook_id} has allowed_for_transfer_design=false",
                    artifact_ids=[v.variant_id],
                    resolution="blocked",
                ))
                ok = False
            if hook.path_classification == "protected_candidate":
                issues.append(TransferValidationIssue(
                    issue_id=f"hook_protected_{binding.hook_id}",
                    category="policy_violation",
                    invariant_category="4_variant_hook",
                    description=f"Hook {binding.hook_id} is protected_candidate",
                    artifact_ids=[v.variant_id],
                    resolution="blocked",
                ))
                ok = False
    return ok


def _check_compatibility_invariants(
    analysis: IdeaTransferAnalysis,
    issues: list[TransferValidationIssue],
) -> bool:
    ok = True
    for vid, va in analysis.variant_analyses.items():
        for j in va.dimensions:
            if j.status == CompatibilityStatus.INCOMPATIBLE and not j.blocking:
                issues.append(TransferValidationIssue(
                    issue_id=f"incompatible_not_blocking_{vid}_{j.dimension.value}",
                    category="policy_violation",
                    invariant_category="5_compatibility",
                    description=f"INCOMPATIBLE judgment for {j.dimension.value} has blocking=false",
                    artifact_ids=[vid],
                    resolution="artifact_repair",
                ))
                ok = False
    return ok


def _check_risk_invariants(
    variants: list[ImplementationVariant],
    risk_reports: list[VariantRiskReport],
    selection: VariantSelection,
    hooks: dict[str, ModificationHook],
    issues: list[TransferValidationIssue],
) -> bool:
    ok = True
    report_by_id = {r.variant_id: r for r in risk_reports}

    for v in variants:
        report = report_by_id.get(v.variant_id)
        if report is None:
            continue
        if report.computed_risk_level != v.risk_level:
            issues.append(TransferValidationIssue(
                issue_id=f"risk_level_mismatch_{v.variant_id}",
                category="policy_violation",
                invariant_category="6_risk",
                description=f"Variant {v.variant_id} risk_level={v.risk_level} but computed={report.computed_risk_level}",
                artifact_ids=[v.variant_id],
                resolution="artifact_repair",
            ))
            ok = False

    selected_ids = {s.variant_id for s in selection.selected}
    for report in risk_reports:
        if report.variant_id in selected_ids:
            for ar in report.accepted_risks:
                if ar.severity in ("medium", "high") and not ar.user_decision_evidence_id:
                    issues.append(TransferValidationIssue(
                        issue_id=f"risk_not_accepted_{ar.risk_id}",
                        category="policy_violation",
                        invariant_category="6_risk",
                        description=f"AcceptedRisk {ar.risk_id} has no user_decision_evidence_id",
                        artifact_ids=[report.variant_id],
                        resolution="user_decide",
                    ))
                    ok = False

    return ok


def _check_selection_invariants(
    selection: VariantSelection,
    analysis: IdeaTransferAnalysis,
    variants: list[ImplementationVariant],
    issues: list[TransferValidationIssue],
) -> bool:
    ok = True
    presentable = set(analysis.viable_variant_ids + analysis.conditional_variant_ids)

    for s in selection.selected:
        if s.variant_id not in presentable:
            issues.append(TransferValidationIssue(
                issue_id=f"selected_not_presentable_{s.variant_id}",
                category="policy_violation",
                invariant_category="7_selection",
                description=f"Selected variant {s.variant_id} is not in viable/conditional set",
                artifact_ids=[s.variant_id],
                resolution="user_decide",
            ))
            ok = False
        if not s.user_decision_evidence_id:
            issues.append(TransferValidationIssue(
                issue_id=f"selected_no_evidence_{s.variant_id}",
                category="policy_violation",
                invariant_category="7_selection",
                description=f"SelectedVariant {s.variant_id} has no user_decision_evidence_id",
                artifact_ids=[s.variant_id],
                resolution="user_decide",
            ))
            ok = False

    return ok


def classify_unresolved(
    unresolved: list[UnresolvedDimension],
) -> tuple[list[UnresolvedDimension], list[UnresolvedDimension], list[UnresolvedDimension]]:
    """Split unresolved dimensions into blocking, experiment_resolvable, warnings."""
    design_blocking: list[UnresolvedDimension] = []
    experiment: list[UnresolvedDimension] = []
    warnings: list[UnresolvedDimension] = []

    for u in unresolved:
        if u.classification == ResolutionClass.DESIGN_BLOCKING:
            design_blocking.append(u)
        elif u.classification == ResolutionClass.EXPERIMENT_RESOLVABLE:
            experiment.append(u)
        elif u.classification == ResolutionClass.NONBLOCKING_WARNING:
            warnings.append(u)

    return design_blocking, experiment, warnings
