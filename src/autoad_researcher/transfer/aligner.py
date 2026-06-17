"""C10: ArchitectureAligner.

Aligns a paper mechanism (or user-provided idea) with the baseline architecture
contract, producing AlignmentEntry list with AlignableScope routing.
"""

from autoad_researcher.schemas.baseline_architecture import BaselineArchitectureContract, ModificationHook
from autoad_researcher.schemas.transfer_design import (
    AlignableScope,
    AlignmentEntry,
    AlignmentStatus,
    IdeaAspectRef,
    IdeaContract,
    PaperGroundedIdeaContract,
    UserProvidedIdeaContract,
)


class AlignerResult:
    """Output of ArchitectureAligner."""

    def __init__(
        self,
        entries: list[AlignmentEntry],
        needs_paper_reanalysis: bool = False,
        needs_repository_reanalysis: bool = False,
        global_incompatible: bool = False,
        skipped_hook_ids: list[str] | None = None,
        skipped_phase_ids: list[str] | None = None,
    ):
        self.entries = entries
        self.needs_paper_reanalysis = needs_paper_reanalysis
        self.needs_repository_reanalysis = needs_repository_reanalysis
        self.global_incompatible = global_incompatible
        self.skipped_hook_ids = skipped_hook_ids or []
        self.skipped_phase_ids = skipped_phase_ids or []


def align_idea_to_baseline(
    idea_contract: IdeaContract,
    baseline_contract: BaselineArchitectureContract,
) -> AlignerResult:
    """Align one confirmed idea with the baseline architecture contract.

    Returns alignment entries and routing signals (reanalysis / incompatible).
    """
    idea_source = idea_contract.idea_source
    entries: list[AlignmentEntry] = []
    needs_paper = False
    needs_repo = False
    global_incompatible = False
    skipped_hooks: list[str] = []
    skipped_phases: list[str] = []

    # Determine aspects based on source type
    if isinstance(idea_source, UserProvidedIdeaContract):
        aspects = _build_user_provided_aspects(idea_source, idea_contract.idea_id)
    else:
        aspects = _build_paper_grounded_aspects(idea_source, idea_contract.idea_id)

    for aspect in aspects:
        entry = _align_aspect(aspect, baseline_contract)
        entries.append(entry)

        # Only the primary mechanism aspect triggers reanalysis;
        # derived_hypothesis aspects with no overlap are expected.
        is_primary = aspect.source_kind in ("paper_grounded", "user_provided")
        is_hypothesis = aspect.source_kind == "derived_hypothesis"

        if entry.match_status == AlignmentStatus.INSUFFICIENT_PAPER_EVIDENCE:
            needs_paper = True
        elif entry.match_status == AlignmentStatus.INSUFFICIENT_REPOSITORY_EVIDENCE:
            if is_primary:
                needs_repo = True
            # derived_hypothesis aspects with no repository overlap = expected, not an error
        elif entry.match_status == AlignmentStatus.INCOMPATIBLE:
            if entry.scope == AlignableScope.GLOBAL_IDEA:
                global_incompatible = True
            elif entry.scope == AlignableScope.SPECIFIC_HOOK:
                skipped_hooks.extend(entry.candidate_hook_ids)
            elif entry.scope == AlignableScope.SPECIFIC_PHASE:
                skipped_phases.extend(entry.baseline_component_ids)

    return AlignerResult(
        entries=entries,
        needs_paper_reanalysis=needs_paper,
        needs_repository_reanalysis=needs_repo,
        global_incompatible=global_incompatible,
        skipped_hook_ids=skipped_hooks,
        skipped_phase_ids=skipped_phases,
    )


def _build_user_provided_aspects(
    source: UserProvidedIdeaContract,
    idea_id: str,
) -> list[IdeaAspectRef]:
    """Build aspects from user-provided idea (no paper evidence)."""
    return [
        IdeaAspectRef(
            aspect_id=f"{idea_id}_user_mechanism",
            label="User Mechanism",
            description=source.user_description,
            source_kind="user_provided",
            evidence_ids=source.user_evidence_ids,
        ),
        IdeaAspectRef(
            aspect_id=f"{idea_id}_user_hypothesis",
            label="Transfer Hypothesis",
            description=source.mechanism_hypothesis.value,
            source_kind="derived_hypothesis",
            evidence_ids=source.mechanism_hypothesis.supporting_evidence_ids,
        ),
    ]


def _build_paper_grounded_aspects(
    source: PaperGroundedIdeaContract,
    idea_id: str,
) -> list[IdeaAspectRef]:
    """Build aspects from paper-grounded idea."""
    return [
        IdeaAspectRef(
            aspect_id=f"{idea_id}_paper_mechanism",
            label="Paper Mechanism",
            description=source.paper_mechanism_summary,
            source_kind="paper_grounded",
            evidence_ids=source.paper_evidence_ids,
        ),
        IdeaAspectRef(
            aspect_id=f"{idea_id}_rationale",
            label="Original Rationale",
            description=source.original_mechanism_rationale.value,
            source_kind="derived_hypothesis",
            evidence_ids=source.original_mechanism_rationale.supporting_evidence_ids,
        ),
    ]


def _align_aspect(
    aspect: IdeaAspectRef,
    contract: BaselineArchitectureContract,
) -> AlignmentEntry:
    """Try to match one idea aspect to baseline components/hooks/tensors.

    Key rules:
    - No keyword overlap → INSUFFICIENT_REPOSITORY_EVIDENCE (NOT incompatible).
    - Scope is derived from what was actually matched.
    - SPECIFIC_HOOK requires non-empty candidate_hook_ids.
    """
    desc_lower = aspect.description.lower()
    candidate_hooks: list[str] = []
    baseline_components: list[str] = []
    baseline_tensors: list[str] = []

    for hook in contract.modifiable_hooks:
        hook_text = (hook.hook_name + " " + hook.semantic_role).lower()
        if _semantic_overlap(desc_lower, hook_text):
            candidate_hooks.append(hook.hook_id)

    for comp in contract.architecture_components:
        comp_text = (comp.name + " " + comp.role + " " + comp.semantic_description).lower()
        if _semantic_overlap(desc_lower, comp_text):
            baseline_components.append(comp.component_id)

    for tensor in contract.tensors:
        tensor_text = (tensor.tensor_name + " " + tensor.semantic_role).lower()
        if _semantic_overlap(desc_lower, tensor_text):
            baseline_tensors.append(tensor.tensor_name)

    # No overlap at all → insufficient evidence, not incompatible
    if not candidate_hooks and not baseline_components and not baseline_tensors:
        return AlignmentEntry(
            idea_aspect=aspect,
            match_status=AlignmentStatus.INSUFFICIENT_REPOSITORY_EVIDENCE,
            scope=AlignableScope.GLOBAL_IDEA,
            rationale=(
                "No keyword-level semantic overlap between idea aspect and baseline "
                "architecture. This does NOT prove incompatibility — it means the "
                "repository evidence is insufficient for alignment. Re-analysis or "
                "Agent-level semantic alignment is required."
            ),
        )

    # Derive scope from what was actually matched
    if candidate_hooks:
        scope = AlignableScope.SPECIFIC_HOOK
    elif baseline_components or baseline_tensors:
        # Components/tensors matched but no hooks → alignment exists but
        # the specific insertion point is not yet identified
        scope = AlignableScope.SPECIFIC_VARIANT_ROUTE
    else:
        scope = AlignableScope.SPECIFIC_VARIANT_ROUTE

    return AlignmentEntry(
        idea_aspect=aspect,
        baseline_component_ids=baseline_components,
        baseline_tensor_ids=baseline_tensors,
        candidate_hook_ids=candidate_hooks,
        match_status=AlignmentStatus.COMPATIBLE,
        scope=scope,
        rationale=(
            f"Found {len(candidate_hooks)} candidate hook(s), "
            f"{len(baseline_components)} component(s), "
            f"{len(baseline_tensors)} tensor(s) with semantic overlap."
        ),
    )


def _semantic_overlap(text_a: str, text_b: str) -> bool:
    """Simple key-token overlap check."""
    tokens_a = set(text_a.split())
    tokens_b = set(text_b.split())
    if not tokens_a or not tokens_b:
        return False
    # Remove very common words
    stop = {"the", "a", "an", "is", "are", "of", "in", "to", "for", "and", "or", "on", "at"}
    tokens_a = tokens_a - stop
    tokens_b = tokens_b - stop
    if not tokens_a or not tokens_b:
        return False
    intersection = tokens_a & tokens_b
    return len(intersection) >= 1
