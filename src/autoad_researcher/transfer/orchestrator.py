"""Step 3.4 orchestrator — wires routing, alignment, compatibility, selection,
validation, and handoff into a runnable pipeline.

Supports: run, resume, budget enforcement, artifact lifecycle.
"""

from datetime import datetime, timezone

from autoad_researcher.schemas.baseline_architecture import BaselineArchitectureContract
from autoad_researcher.schemas.transfer_design import (
    IdeaContract,
    IdeaTransferAnalysis,
    IdeaTransferBudget,
    IdeaTransferDesignHandoff,
    ImplementationVariant,
    TransferConstraint,
    TransferResumeFingerprint,
    TransferStatus,
    UnresolvedDimension,
    VariantRiskReport,
    VariantSelection,
)
from autoad_researcher.transfer.aligner import align_idea_to_baseline
from autoad_researcher.transfer.compatibility import analyze_all_variants, filter_variants
from autoad_researcher.transfer.handoff import build_handoff
from autoad_researcher.transfer.normalizer import confirm_idea_contract, normalize_idea_contract
from autoad_researcher.transfer.reanalysis import (
    build_paper_reanalysis,
    build_repository_reanalysis,
    build_spawn_child_run,
)
from autoad_researcher.transfer.risks import build_variant_risk_report
from autoad_researcher.transfer.router import resolve_paper_candidates, route_user_idea, route_user_original_idea
from autoad_researcher.transfer.selector import (
    is_blocked_no_selection,
    recommend_variants,
    select_variants,
)
from autoad_researcher.transfer.validator import classify_unresolved, validate_transfer
from autoad_researcher.transfer.variants import generate_variants


class StepStatus:
    """Step 3.4 execution status."""

    def __init__(
        self,
        stage: str = "idle",
        blocked: bool = False,
        blocked_reason: str | None = None,
        idea_contract: IdeaContract | None = None,
        analysis: IdeaTransferAnalysis | None = None,
        variants: list[ImplementationVariant] | None = None,
        selection: VariantSelection | None = None,
        risk_reports: list[VariantRiskReport] | None = None,
        reanalysis_request: dict | None = None,
        spawn_child_request: dict | None = None,
        handoff: IdeaTransferDesignHandoff | None = None,
        budget: IdeaTransferBudget | None = None,
    ):
        self.stage = stage
        self.blocked = blocked
        self.blocked_reason = blocked_reason
        self.idea_contract = idea_contract
        self.analysis = analysis
        self.variants = variants or []
        self.selection = selection
        self.risk_reports = risk_reports or []
        self.reanalysis_request = reanalysis_request
        self.spawn_child_request = spawn_child_request
        self.handoff = handoff
        self.budget = budget or IdeaTransferBudget()


def run_idea_transfer_design(
    run_id: str,
    source_context_id: str,
    source_context_version: int,
    source_context_sha256: str,
    idea_transfer_handoff: dict,
    baseline_contract: BaselineArchitectureContract | None,
    paper_idea_sources: list[dict],
    budget: IdeaTransferBudget | None = None,
) -> StepStatus:
    """Execute the full Step 3.4 pipeline.

    Returns a StepStatus that indicates the current stage and whether
    the pipeline is blocked waiting for user input, reanalysis, or is ready
    for Step 3.5.
    """
    bgt = budget or IdeaTransferBudget()
    status = StepStatus(budget=bgt)

    # --- Check baseline contract ---
    if baseline_contract is None:
        status.stage = "waiting_for_baseline_contract"
        status.blocked = True
        status.blocked_reason = "No baseline_architecture_contract available. Repository reanalysis required."
        status.reanalysis_request = {
            "type": "repository",
            "data": build_repository_reanalysis(
                run_id=run_id,
                reason="Missing baseline_architecture_contract",
                missing_artifacts=["baseline_architecture_contract.json"],
            ).model_dump(),
        }
        return status

    # --- Route idea source ---
    user_idea_label = idea_transfer_handoff.get("user_idea_label")
    eligible_sources = paper_idea_sources

    if user_idea_label:
        result = route_user_idea(user_idea_label, eligible_sources, f"ev_user_input_{run_id}")
        if result.route == "A_paper_grounded" and result.idea_source is not None:
            source = result.idea_source
        elif result.route == "A_user_provided" and result.idea_source is not None:
            source = result.idea_source
        elif result.route == "A_fuzzy_match_needs_confirmation":
            status.stage = "waiting_for_idea_confirmation"
            status.blocked = True
            status.blocked_reason = result.blocked_reason
            return status
        elif result.route == "A_not_found":
            # Could be paper reanalysis or user original idea
            status.stage = "waiting_for_idea_source"
            status.blocked = True
            status.blocked_reason = result.blocked_reason
            return status
        else:
            status.stage = "waiting_for_idea_source"
            status.blocked = True
            status.blocked_reason = "Could not route idea source"
            return status
    else:
        # Path B: present paper candidates
        hook_names = [h.hook_name for h in baseline_contract.modifiable_hooks]
        result = resolve_paper_candidates(eligible_sources, baseline_contract_hooks=hook_names)
        if result.blocked:
            status.stage = "waiting_for_idea_source"
            status.blocked = True
            status.blocked_reason = result.blocked_reason
            return status
        # Candidates ready, user must select
        status.stage = "waiting_for_idea_confirmation"
        status.blocked = True
        status.blocked_reason = f"{len(result.candidates)} candidate(s) available. User must select one."
        return status

    # --- Normalize idea contract ---
    idea = normalize_idea_contract(
        idea_id=f"idea_{run_id}",
        idea_source=source,
        confirmed_by_user=True,
        confirmation_evidence_id=f"ev_confirm_{run_id}",
    )
    status.idea_contract = idea

    # --- Architecture alignment ---
    aligner_result = align_idea_to_baseline(idea, baseline_contract)
    if aligner_result.needs_paper_reanalysis:
        status.stage = "reanalysis_requested"
        status.blocked = True
        status.blocked_reason = "Paper reanalysis required"
        status.reanalysis_request = {
            "type": "paper",
            "data": build_paper_reanalysis(
                run_id=run_id,
                reason="Insufficient paper evidence for alignment",
            ).model_dump(),
        }
        return status
    if aligner_result.needs_repository_reanalysis:
        status.stage = "reanalysis_requested"
        status.blocked = True
        status.blocked_reason = "Repository reanalysis required"
        status.reanalysis_request = {
            "type": "repository",
            "data": build_repository_reanalysis(
                run_id=run_id,
                reason="Insufficient repository evidence for alignment",
            ).model_dump(),
        }
        return status
    if aligner_result.global_incompatible:
        status.stage = "non_viable"
        status.blocked = True
        status.blocked_reason = "Idea is globally incompatible with baseline"
        status.spawn_child_request = build_spawn_child_run(
            parent_run_id=run_id,
            reason="parent_idea_non_viable",
        ).model_dump()
        return status

    # --- Generate variants ---
    valid_hooks = list(set(
        h for entry in aligner_result.entries
        for h in entry.candidate_hook_ids
        if h not in aligner_result.skipped_hook_ids
    ))
    if not valid_hooks:
        valid_hooks = [h.hook_id for h in baseline_contract.modifiable_hooks][:bgt.max_variants]

    variants = generate_variants(idea, valid_hooks, max_variants=bgt.max_variants)
    if not variants:
        status.stage = "non_viable"
        status.blocked = True
        status.blocked_reason = "No viable variants generated"
        return status
    status.variants = variants

    # --- Per-variant compatibility ---
    constraints: list[TransferConstraint] = []
    analysis = analyze_all_variants(variants, constraints)
    presentable, non_viable, needs_reanalysis, all_need = filter_variants(analysis)

    if all_need:
        status.stage = "all_variants_need_reanalysis"
        status.blocked = True
        status.blocked_reason = "All variants need reanalysis"
        status.reanalysis_request = {
            "type": "repository",
            "data": build_repository_reanalysis(
                run_id=run_id,
                reason="All variants need repository reanalysis",
            ).model_dump(),
        }
        return status
    if not presentable and non_viable:
        status.stage = "non_viable"
        status.blocked = True
        status.blocked_reason = "All variants are non-viable"
        status.spawn_child_request = build_spawn_child_run(
            parent_run_id=run_id,
            reason="parent_idea_non_viable",
        ).model_dump()
        return status
    if not presentable:
        status.stage = "non_viable"
        status.blocked = True
        status.blocked_reason = "No presentable variants"
        return status
    status.analysis = analysis

    # --- Recommend variants (user must select) ---
    selection = recommend_variants(
        variants=variants,
        presentable_ids=presentable,
        non_viable_ids=non_viable,
        needs_reanalysis_ids=needs_reanalysis,
        idea_id=idea.idea_id,
    )
    status.selection = selection

    if not selection.selected:
        status.stage = "waiting_for_variant_selection"
        status.blocked = True
        status.blocked_reason = f"{len(presentable)} variant(s) available. User must select."
        return status

    # --- When user has confirmed, continue to validation and handoff ---
    return status


def finalize_transfer_design(
    status: StepStatus,
    run_id: str,
    source_context_id: str,
    source_context_version: int,
    source_context_sha256: str,
    baseline_contract: BaselineArchitectureContract,
    user_evidence_id: str,
    selected_variant_ids: list[str] | None = None,
    validator_report_sha256: str | None = None,
    unresolved_dimensions: list[UnresolvedDimension] | None = None,
) -> StepStatus:
    """Finalize after user confirms variant selection.

    Runs validation, risk reporting, and handoff build.
    """
    if status.idea_contract is None:
        status.stage = "idea_not_confirmed"
        status.blocked = True
        return status

    # Select variants if user provided IDs
    if selected_variant_ids and status.selection:
        status.selection = select_variants(status.selection, selected_variant_ids, user_evidence_id)

    if status.selection is None:
        status.stage = "no_selection"
        status.blocked = True
        return status

    if is_blocked_no_selection(status.selection):
        status.stage = "blocked_no_variant_selected"
        status.blocked = True
        return status

    # Build risk reports and reconcile variant risk_level
    hooks_dict = {h.hook_id: h for h in baseline_contract.modifiable_hooks}
    risk_reports: list[VariantRiskReport] = []
    for v in status.variants:
        judgments = []
        if status.analysis and v.variant_id in status.analysis.variant_analyses:
            judgments = status.analysis.variant_analyses[v.variant_id].dimensions
        report = build_variant_risk_report(v, judgments, hooks_dict)
        risk_reports.append(report)
        # Reconcile variant.risk_level with computed value
        if v.risk_level != report.computed_risk_level:
            v.risk_level = report.computed_risk_level
    status.risk_reports = risk_reports

    # Validate
    resolved = unresolved_dimensions or []
    report = validate_transfer(
        run_id=run_id,
        idea_contract=status.idea_contract,
        baseline_contract=baseline_contract,
        analysis=status.analysis or IdeaTransferAnalysis(idea_id=status.idea_contract.idea_id, variant_analyses={}),
        selection=status.selection,
        variants=status.variants,
        risk_reports=risk_reports,
        hooks=hooks_dict,
        resolved_dimensions=resolved,
    )

    if report.status == "failed":
        status.stage = "validation_failed"
        status.blocked = True
        status.blocked_reason = f"Validation failed with {len(report.issues)} issues"
        return status

    report_sha = _sha256(report.model_dump_json())
    final_sha = validator_report_sha256 or report_sha

    # Build handoff
    design_blocking, experiment, warnings = classify_unresolved(resolved)
    if design_blocking:
        status.stage = "design_blocking_in_handoff"
        status.blocked = True
        status.blocked_reason = f"{len(design_blocking)} design_blocking unresolved dimensions"
        return status

    try:
        handoff = build_handoff(
            run_id=run_id,
            source_context_id=source_context_id,
            source_context_version=source_context_version,
            source_context_sha256=source_context_sha256,
            idea_contract=status.idea_contract,
            transfer_analysis=status.analysis or IdeaTransferAnalysis(
                idea_id=status.idea_contract.idea_id,
                variant_analyses={},
            ),
            transfer_constraints=[],
            selected_variants=[v for v in status.variants if any(
                s.variant_id == v.variant_id for s in (status.selection.selected if status.selection else [])
            )],
            risk_reports=risk_reports,
            unresolved_dimensions=resolved,
            validator_report_sha256=final_sha,
        )
        status.handoff = handoff
        status.stage = "ready_for_3_5"
        status.blocked = False
    except ValueError as e:
        status.stage = "handoff_failed"
        status.blocked = True
        status.blocked_reason = str(e)

    return status


import hashlib


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()
