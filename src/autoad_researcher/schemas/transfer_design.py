"""Step 3.4 — Transfer Design schemas.

Covers:
  - C2: IdeaContract (discriminated union), DerivedClaim, ConstraintRef
  - C3: IdeaAspectRef, AlignmentEntry, AlignmentStatus, AlignableScope
  - C4: TransferConstraint, ConstraintStrength, TransferStatus
  - C5: DIMENSION_POLICY, DimensionJudgment, derives_variant_status
  - C6: TensorContractDelta, InterfaceContractDelta, RegimeChange
  - C7: HookBinding, ImplementationVariant
  - C8+: Selection, Risk, Validation, Reanalysis, Handoff

Schema owner: Step 3.4 Idea & Transfer Design.
Consumer: Step 3.5 Multi-variant Experiment Planner (handoff).
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.functional_validators import AfterValidator

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern
from autoad_researcher.schemas.baseline_architecture import (
    ExecutionPhaseContract,
    TensorSpec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _non_empty_list(v: list) -> list:
    if len(v) < 1:
        raise ValueError("list must not be empty")
    return v


NonEmptyStr = Annotated[str, AfterValidator(lambda v: _check_nonempty(v))]


def _check_nonempty(v: str) -> str:
    if not v or not v.strip():
        raise ValueError("string must not be empty")
    return v


# ---------------------------------------------------------------------------
# C2: IdeaContract — discriminated union
# ---------------------------------------------------------------------------


class DerivedClaim(BaseModel):
    """A Step 3.4 analytical claim, NOT a fact. Always inferred."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    status: Literal["inferred"] = "inferred"
    producer_stage: Literal["3.4"] = "3.4"
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ConstraintRef(BaseModel):
    """A must-preserve behavior with traceable source."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    source: Literal["user_provided", "paper_derived", "baseline_derived"]
    evidence_ids: list[str] = Field(default_factory=list)


class UserProvidedIdeaContract(BaseModel):
    """Idea sourced from user, no paper evidence required."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["user_provided"] = "user_provided"
    user_description: str = Field(min_length=1)
    user_evidence_ids: list[str] = Field(default_factory=list)
    mechanism_hypothesis: DerivedClaim
    transfer_relevance: DerivedClaim


class PaperGroundedIdeaContract(BaseModel):
    """Idea sourced from paper, requires PaperEvidenceRef."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["paper_grounded"] = "paper_grounded"
    paper_idea_source_id: str = Field(min_length=1)
    paper_mechanism_summary: str = Field(min_length=1)
    paper_evidence_ids: list[str] = Field(min_length=1)
    original_mechanism_rationale: DerivedClaim
    transfer_relevance: DerivedClaim


IdeaContractCore = Annotated[
    Union[UserProvidedIdeaContract, PaperGroundedIdeaContract],
    Field(discriminator="source"),
]


class IdeaContract(BaseModel):
    """Confirmed research idea for one run."""

    model_config = ConfigDict(extra="forbid")

    idea_id: str = Field(pattern=IdentifierPattern)
    idea_source: IdeaContractCore
    must_preserve_behaviors: list[ConstraintRef] = Field(default_factory=list)
    confirmation_status: Literal["confirmed", "pending"]
    confirmed_by_user_at: datetime | None = None
    confirmation_evidence_id: str | None = None
    supersedes_idea_id: str | None = None

    @model_validator(mode="after")
    def _confirmed_requires_timestamp(self):
        if self.confirmation_status == "confirmed":
            if self.confirmed_by_user_at is None:
                raise ValueError("confirmed idea requires confirmed_by_user_at")
            if self.confirmation_evidence_id is None:
                raise ValueError("confirmed idea requires confirmation_evidence_id")
        return self


# ---------------------------------------------------------------------------
# C3: Alignment
# ---------------------------------------------------------------------------


class IdeaAspectRef(BaseModel):
    """Source-neutral reference to one aspect of the idea."""

    model_config = ConfigDict(extra="forbid")

    aspect_id: str = Field(pattern=IdentifierPattern)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_kind: Literal["paper_grounded", "user_provided", "derived_hypothesis"]
    evidence_ids: list[str] = Field(default_factory=list)


class AlignmentStatus(str, Enum):
    COMPATIBLE = "compatible"
    POSSIBLE_WITH_ADAPTER = "possible_with_adapter"
    REQUIRES_REGIME_CHANGE = "requires_regime_change"
    INSUFFICIENT_PAPER_EVIDENCE = "insufficient_paper_evidence"
    INSUFFICIENT_REPOSITORY_EVIDENCE = "insufficient_repository_evidence"
    INCOMPATIBLE = "incompatible"


class AlignableScope(str, Enum):
    GLOBAL_IDEA = "global_idea"
    SPECIFIC_HOOK = "specific_hook"
    SPECIFIC_PHASE = "specific_phase"
    SPECIFIC_VARIANT_ROUTE = "specific_variant_route"


class AlignmentEntry(BaseModel):
    """One alignment row: idea aspect ↔ baseline component/hook/tensor."""

    model_config = ConfigDict(extra="forbid")

    idea_aspect: IdeaAspectRef
    baseline_component_ids: list[str] = Field(default_factory=list)
    baseline_tensor_ids: list[str] = Field(default_factory=list)
    candidate_hook_ids: list[str] = Field(default_factory=list)
    match_status: AlignmentStatus
    scope: AlignableScope
    rationale: str = Field(min_length=1)
    repository_evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# C4: TransferConstraint + TransferStatus
# ---------------------------------------------------------------------------


class CompatibilityDimension(str, Enum):
    SEMANTIC = "semantic"
    INPUT = "input"
    OUTPUT = "output"
    SHAPE = "shape"
    TRAINING = "training"
    DATA = "data"
    LABEL = "label"
    EVALUATION = "evaluation"
    RESOURCE = "resource"


class CompatibilityStatus(str, Enum):
    COMPATIBLE = "compatible"
    COMPATIBLE_WITH_ADAPTER = "compatible_with_adapter"
    REQUIRES_REGIME_CHANGE = "requires_regime_change"
    INCOMPATIBLE = "incompatible"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ConstraintStrength(str, Enum):
    HARD = "hard"
    USER_CONFIRMED = "user_confirmed"
    SOFT = "soft"


class TransferConstraint(BaseModel):
    """A hard or soft constraint on the transfer."""

    model_config = ConfigDict(extra="forbid")

    constraint_id: str = Field(pattern=IdentifierPattern)
    category: CompatibilityDimension
    description: str = Field(min_length=1)
    strength: ConstraintStrength
    prohibited_changes: list[str] = Field(default_factory=list)
    required_properties: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    user_decision_evidence_id: str | None = None


class TransferStatus(str, Enum):
    VIABLE = "viable"
    VIABLE_WITH_CONDITIONS = "viable_with_conditions"
    NON_VIABLE = "non_viable"
    NEEDS_REANALYSIS = "needs_reanalysis"


# ---------------------------------------------------------------------------
# C5: DimensionJudgment + DIMENSION_POLICY + derive
# ---------------------------------------------------------------------------


class EvidenceStrategy(str, Enum):
    REANALYSIS = "reanalysis"
    DESIGN_BLOCKING = "design_blocking"
    EXPERIMENT_RESOLVABLE = "experiment_resolvable"
    SOFT_WARNING = "soft_warning"


DIMENSION_POLICY: dict[CompatibilityDimension, EvidenceStrategy] = {
    CompatibilityDimension.INPUT:       EvidenceStrategy.REANALYSIS,
    CompatibilityDimension.OUTPUT:      EvidenceStrategy.REANALYSIS,
    CompatibilityDimension.SHAPE:       EvidenceStrategy.REANALYSIS,
    CompatibilityDimension.TRAINING:    EvidenceStrategy.REANALYSIS,
    CompatibilityDimension.DATA:        EvidenceStrategy.DESIGN_BLOCKING,
    CompatibilityDimension.LABEL:       EvidenceStrategy.DESIGN_BLOCKING,
    CompatibilityDimension.EVALUATION:  EvidenceStrategy.DESIGN_BLOCKING,
    CompatibilityDimension.RESOURCE:    EvidenceStrategy.EXPERIMENT_RESOLVABLE,
    CompatibilityDimension.SEMANTIC:    EvidenceStrategy.DESIGN_BLOCKING,
}


class DimensionJudgment(BaseModel):
    """One dimension's compatibility judgment for a specific variant."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    dimension: CompatibilityDimension
    status: CompatibilityStatus
    blocking: bool
    reasoning: str = Field(min_length=1)
    idea_contract_evidence_ids: list[str] = Field(default_factory=list)
    paper_evidence_ids: list[str] = Field(default_factory=list)
    repository_evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    required_adapter: str | None = None
    risk: Literal["low", "medium", "high"] | None = None

    @model_validator(mode="after")
    def _incompatible_must_be_blocking(self):
        if self.status == CompatibilityStatus.INCOMPATIBLE and not self.blocking:
            raise ValueError("INCOMPATIBLE judgment must have blocking=True")
        if self.status == CompatibilityStatus.COMPATIBLE and self.blocking:
            raise ValueError("COMPATIBLE judgment cannot be blocking")
        return self


def violates_confirmed_constraint(
    judgment: DimensionJudgment,
    constraints: list[TransferConstraint],
) -> bool:
    """Check if a judgment violates a hard or user-confirmed constraint.

    Only inspects structured fields (required_changes), not free-text reasoning.
    """
    for c in constraints:
        if c.strength not in (ConstraintStrength.HARD, ConstraintStrength.USER_CONFIRMED):
            continue
        if c.category != judgment.dimension:
            continue
        for req in c.prohibited_changes:
            if req in judgment.required_changes:
                return True
    return False


def derive_variant_status(
    judgments: list[DimensionJudgment],
    constraints: list[TransferConstraint],
) -> TransferStatus:
    """Deterministically derive per-variant transfer status."""
    # 1. INSUFFICIENT_EVIDENCE → route by DIMENSION_POLICY
    for j in judgments:
        if j.status == CompatibilityStatus.INSUFFICIENT_EVIDENCE:
            strategy = DIMENSION_POLICY.get(j.dimension, EvidenceStrategy.SOFT_WARNING)
            if strategy == EvidenceStrategy.REANALYSIS:
                return TransferStatus.NEEDS_REANALYSIS
            elif strategy == EvidenceStrategy.DESIGN_BLOCKING:
                return TransferStatus.NON_VIABLE

    # 2. INCOMPATIBLE → non_viable
    if any(j.status == CompatibilityStatus.INCOMPATIBLE for j in judgments):
        return TransferStatus.NON_VIABLE

    # 3. REQUIRES_REGIME_CHANGE that violates a confirmed constraint → non_viable
    if any(
        j.status == CompatibilityStatus.REQUIRES_REGIME_CHANGE
        and violates_confirmed_constraint(j, constraints)
        for j in judgments
    ):
        return TransferStatus.NON_VIABLE

    # 4. Any non-compatible → viable_with_conditions
    if any(
        j.status in {
            CompatibilityStatus.COMPATIBLE_WITH_ADAPTER,
            CompatibilityStatus.REQUIRES_REGIME_CHANGE,
            CompatibilityStatus.INSUFFICIENT_EVIDENCE,
        }
        for j in judgments
    ):
        return TransferStatus.VIABLE_WITH_CONDITIONS

    # 5. All compatible → viable
    return TransferStatus.VIABLE


class VariantTransferAnalysis(BaseModel):
    """Per-variant transfer compatibility analysis."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    dimensions: list[DimensionJudgment] = Field(default_factory=list)
    overall_status: TransferStatus
    constraints: list[TransferConstraint] = Field(default_factory=list)
    unresolved_dimensions: list["UnresolvedDimension"] = Field(default_factory=list)


class IdeaTransferAnalysis(BaseModel):
    """Aggregate analysis for one idea across all variants."""

    model_config = ConfigDict(extra="forbid")

    idea_id: str = Field(pattern=IdentifierPattern)
    variant_analyses: dict[str, VariantTransferAnalysis] = Field(default_factory=dict)
    viable_variant_ids: list[str] = Field(default_factory=list)
    conditional_variant_ids: list[str] = Field(default_factory=list)
    non_viable_variant_ids: list[str] = Field(default_factory=list)
    needs_reanalysis_variant_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# C6: Structured tensor / interface deltas
# ---------------------------------------------------------------------------


class TensorContractDelta(BaseModel):
    """Structured delta for one tensor before/after transfer."""

    model_config = ConfigDict(extra="forbid")

    tensor_id: str | None = None
    operation: Literal["unchanged", "added", "removed", "modified"]
    before: TensorSpec | None = None
    after: TensorSpec | None = None
    changed_axes: list[str] = Field(default_factory=list)


class InterfaceContractDelta(BaseModel):
    """Structured delta for one interface before/after transfer."""

    model_config = ConfigDict(extra="forbid")

    interface_id: str | None = None
    input_deltas: list[TensorContractDelta] = Field(default_factory=list)
    output_deltas: list[TensorContractDelta] = Field(default_factory=list)


class RegimeChange(BaseModel):
    """Execution phase change for a variant."""

    model_config = ConfigDict(extra="forbid")

    phase_id: str = Field(min_length=1)
    before_phase: ExecutionPhaseContract | None = None
    after_phase: ExecutionPhaseContract | None = None
    gradient_required: bool = False
    state_mutation_required: bool = False


class StateChangeDescription(BaseModel):
    """Description of a state mutation in a variant."""

    model_config = ConfigDict(extra="forbid")

    state_name: str = Field(min_length=1)
    change_type: Literal["added", "modified", "replaced"]
    description: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# C7: HookBinding + ImplementationVariant
# ---------------------------------------------------------------------------


class HookBinding(BaseModel):
    """Bind a variant to a specific baseline ModificationHook."""

    model_config = ConfigDict(extra="forbid")

    hook_id: str = Field(pattern=IdentifierPattern)
    role: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ImplementationVariant(BaseModel):
    """Architectural-level variant (NO file-level patch plan)."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    variant_label: str = Field(min_length=1)
    idea_id: str = Field(pattern=IdentifierPattern)

    hook_bindings: list[HookBinding] = Field(default_factory=list)
    primary_hook_id: str = Field(min_length=1)

    interface_deltas: list[InterfaceContractDelta] = Field(default_factory=list)
    regime_changes: list[RegimeChange] = Field(default_factory=list)
    state_changes: list[StateChangeDescription] = Field(default_factory=list)

    adapter_required: bool = False
    adapter_description: str | None = None

    new_dependencies: list[str] = Field(default_factory=list)

    expected_behavior_rationale: str = Field(min_length=1)
    risk_level: Literal["low", "medium", "high"]
    fallback_behavior: str = Field(min_length=1)

    idea_contract_evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Risk models
# ---------------------------------------------------------------------------

_RISK_ORDER: dict[str, int] = {"low": 1, "medium": 2, "high": 3}


def compute_variant_risk(
    variant: "ImplementationVariant",
    judgments: list[DimensionJudgment],
    hooks: dict[str, "ModificationHook"],
) -> Literal["low", "medium", "high"]:
    """Compute variant risk_level from structured inputs, not LLM self-report."""
    from autoad_researcher.schemas.baseline_architecture import ModificationHook as _MH  # noqa: F811

    risks: list[str] = []

    for j in judgments:
        if j.risk:
            risks.append(j.risk)

    for b in variant.hook_bindings:
        hook = hooks.get(b.hook_id)
        if hook is None:
            continue
        if hook.path_classification == "protected_candidate":
            risks.append("high")
        elif hook.path_classification == "unknown":
            risks.append("medium")

    if variant.regime_changes:
        risks.append("medium")

    if variant.new_dependencies:
        risks.append("medium")

    if not risks:
        return "low"

    max_severity = max(_RISK_ORDER[r] for r in risks)
    for k, v in _RISK_ORDER.items():
        if v == max_severity:
            return k  # type: ignore[return-value]
    return "low"


class RiskRecord(BaseModel):
    """All identified risks (any severity)."""

    model_config = ConfigDict(extra="forbid")

    risk_id: str = Field(pattern=IdentifierPattern)
    variant_id: str = Field(pattern=IdentifierPattern)
    dimension: CompatibilityDimension
    description: str = Field(min_length=1)
    severity: Literal["low", "medium", "high"]
    evidence_ids: list[str] = Field(default_factory=list)


class AcceptedRisk(BaseModel):
    """A medium/high risk explicitly accepted by the user."""

    model_config = ConfigDict(extra="forbid")

    risk_id: str = Field(pattern=IdentifierPattern)
    variant_id: str = Field(pattern=IdentifierPattern)
    severity: Literal["medium", "high"]
    accepted_by_user: bool
    user_decision_evidence_id: str = Field(min_length=1)
    accepted_at: datetime


class VariantRiskReport(BaseModel):
    """Per-variant risk rollup."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    computed_risk_level: Literal["low", "medium", "high"]
    records: list[RiskRecord] = Field(default_factory=list)
    accepted_risks: list[AcceptedRisk] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# UnresolvedDimension + ClassificationRules
# ---------------------------------------------------------------------------


class ResolutionClass(str, Enum):
    DESIGN_BLOCKING = "design_blocking"
    EXPERIMENT_RESOLVABLE = "experiment_resolvable"
    NONBLOCKING_WARNING = "nonblocking_warning"


class UnresolvedDimension(BaseModel):
    """One dimension that could not be fully resolved for a variant."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    dimension: CompatibilityDimension
    status: CompatibilityStatus
    classification: ResolutionClass
    resolution_reason: str = Field(min_length=1)
    verification_target: str | None = None
    acceptance_criterion: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    classified_by_rule_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _experiment_resolvable_needs_verification(self):
        if self.classification == ResolutionClass.EXPERIMENT_RESOLVABLE:
            if not self.verification_target:
                raise ValueError("experiment_resolvable requires verification_target")
            if not self.acceptance_criterion:
                raise ValueError("experiment_resolvable requires acceptance_criterion")
        return self


CLASSIFICATION_RULES: dict[tuple[CompatibilityDimension, CompatibilityStatus], ResolutionClass] = {
    (CompatibilityDimension.INPUT, CompatibilityStatus.INSUFFICIENT_EVIDENCE): ResolutionClass.DESIGN_BLOCKING,
    (CompatibilityDimension.OUTPUT, CompatibilityStatus.INSUFFICIENT_EVIDENCE): ResolutionClass.DESIGN_BLOCKING,
    (CompatibilityDimension.SHAPE, CompatibilityStatus.INSUFFICIENT_EVIDENCE): ResolutionClass.DESIGN_BLOCKING,
    (CompatibilityDimension.TRAINING, CompatibilityStatus.INSUFFICIENT_EVIDENCE): ResolutionClass.DESIGN_BLOCKING,
    (CompatibilityDimension.DATA, CompatibilityStatus.INCOMPATIBLE): ResolutionClass.DESIGN_BLOCKING,
    (CompatibilityDimension.LABEL, CompatibilityStatus.INCOMPATIBLE): ResolutionClass.DESIGN_BLOCKING,
    (CompatibilityDimension.EVALUATION, CompatibilityStatus.INSUFFICIENT_EVIDENCE): ResolutionClass.DESIGN_BLOCKING,
    (CompatibilityDimension.RESOURCE, CompatibilityStatus.INSUFFICIENT_EVIDENCE): ResolutionClass.EXPERIMENT_RESOLVABLE,
    (CompatibilityDimension.SEMANTIC, CompatibilityStatus.INCOMPATIBLE): ResolutionClass.DESIGN_BLOCKING,
}


# ---------------------------------------------------------------------------
# VariantSelection
# ---------------------------------------------------------------------------


class SelectedVariant(BaseModel):
    """A variant explicitly selected by the user."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    decision: Literal["selected"] = "selected"
    user_decision_evidence_id: str = Field(min_length=1)
    selected_at: datetime


class RejectedVariant(BaseModel):
    """A variant rejected (by user or by system)."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=IdentifierPattern)
    reason: Literal["user_rejected", "non_viable", "needs_reanalysis", "superseded"]
    evidence_ids: list[str] = Field(default_factory=list)


class VariantSelection(BaseModel):
    """User's variant selection, append-only."""

    model_config = ConfigDict(extra="forbid")

    selection_id: str = Field(pattern=IdentifierPattern)
    idea_id: str = Field(pattern=IdentifierPattern)
    selected: list[SelectedVariant] = Field(default_factory=list)
    rejected: list[RejectedVariant] = Field(default_factory=list)
    recommended_variant_ids: list[str] = Field(default_factory=list)
    confirmation_status: Literal["pending", "confirmed"]
    previous_selection_id: str | None = None

    @model_validator(mode="after")
    def _selected_must_not_be_rejected(self):
        selected_ids = {s.variant_id for s in self.selected}
        for r in self.rejected:
            if r.variant_id in selected_ids:
                raise ValueError(f"variant {r.variant_id} is both selected and rejected")
        return self

    @model_validator(mode="after")
    def _confirmed_requires_selections(self):
        if self.confirmation_status == "confirmed":
            if not self.selected:
                raise ValueError("confirmed selection must have at least one selected variant")
        return self


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TransferValidationIssue(BaseModel):
    """One issue found by the deterministic validator."""

    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(pattern=IdentifierPattern)
    category: Literal["schema_repairable", "semantic_contract_invalid", "policy_violation"]
    invariant_category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    artifact_ids: list[str] = Field(default_factory=list)
    resolution: Literal["artifact_repair", "user_decide", "reanalysis_needed", "blocked"]


class IdeaTransferValidationReport(BaseModel):
    """Result of the deterministic validator run."""

    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    status: Literal["passed", "failed", "partial_repair_successful"]
    issues: list[TransferValidationIssue] = Field(default_factory=list)
    invariant_results: dict[str, bool] = Field(default_factory=dict)
    revalidated_at: datetime


# ---------------------------------------------------------------------------
# Reanalysis requests
# ---------------------------------------------------------------------------


class RepositoryReanalysisRequest(BaseModel):
    """Request Step 3.1 to re-analyze for missing architecture evidence."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    reason: str = Field(min_length=1)
    missing_artifacts: list[str] = Field(default_factory=list)
    target_components: list[str] = Field(default_factory=list)
    target_hooks: list[str] = Field(default_factory=list)
    target_tensors: list[str] = Field(default_factory=list)
    required_evidence_types: list[str] = Field(default_factory=list)
    current_contract_sha256: str | None = None
    completion_conditions: list[str] = Field(default_factory=list)
    allowed_read_scope: list[str] = Field(default_factory=list)


class PaperReanalysisRequest(BaseModel):
    """Request Step 3.2 to re-analyze for missing paper evidence."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=IdentifierPattern)
    run_id: str = Field(pattern=IdentifierPattern)
    reason: str = Field(min_length=1)
    missing_fields: list[str] = Field(default_factory=list)
    target_method_ids: list[str] = Field(default_factory=list)
    required_evidence_types: list[str] = Field(default_factory=list)
    current_evidence_ids: list[str] = Field(default_factory=list)
    completion_conditions: list[str] = Field(default_factory=list)


class SpawnChildRunRequest(BaseModel):
    """Request a new child run for a different idea."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=IdentifierPattern)
    parent_run_id: str = Field(pattern=IdentifierPattern)
    reason: Literal["parent_idea_non_viable", "user_wants_alternative_idea"]
    inherited_artifacts: list[str] = Field(default_factory=list)
    new_idea_label: str | None = None


# ---------------------------------------------------------------------------
# ResumeFingerprint
# ---------------------------------------------------------------------------


class TransferResumeFingerprint(BaseModel):
    """Fingerprint of upstream artifacts; change → downstream invalidation."""

    model_config = ConfigDict(extra="forbid")

    research_context_sha256: str = Field(pattern=Sha256Pattern)
    baseline_architecture_contract_sha256: str = Field(pattern=Sha256Pattern)
    paper_idea_sources_sha256: str = Field(pattern=Sha256Pattern)
    method_components_sha256: str = Field(pattern=Sha256Pattern)
    idea_contract_sha256: str = Field(pattern=Sha256Pattern)
    skill_sha256: str = Field(pattern=Sha256Pattern)
    policy_sha256: str = Field(pattern=Sha256Pattern)
    model_profile_sha256: str = Field(pattern=Sha256Pattern)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


class IdeaTransferBudget(BaseModel):
    """Budget for the 3.4 transfer design stage."""

    model_config = ConfigDict(extra="forbid")

    max_llm_calls: int = 20
    max_alignment_routes: int = 5
    max_variants: int = 3
    max_reanalysis_rounds: int = 1
    max_user_confirmation_rounds: int = 3
    max_schema_repairs: int = 2
    max_artifact_repair_llm_calls: int = 4


# ---------------------------------------------------------------------------
# Handoff to Step 3.5
# ---------------------------------------------------------------------------


class IdeaTransferDesignHandoff(BaseModel):
    """Structured handoff from Step 3.4 to Step 3.5."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1

    run_id: str = Field(pattern=IdentifierPattern)
    source_context_id: str = Field(min_length=1)
    source_context_version: int = Field(ge=0)
    source_context_sha256: str = Field(pattern=Sha256Pattern)

    confirmed_idea: IdeaContract
    idea_contract_sha256: str = Field(pattern=Sha256Pattern)

    transfer_analysis: IdeaTransferAnalysis
    transfer_constraints: list[TransferConstraint] = Field(default_factory=list)

    selected_variants: list[ImplementationVariant] = Field(default_factory=list)
    variant_selection_sha256: str = Field(pattern=Sha256Pattern)

    variant_risk_reports: list[VariantRiskReport] = Field(default_factory=list)

    experiment_resolvable_dimensions: list[UnresolvedDimension] = Field(default_factory=list)
    nonblocking_warnings: list[UnresolvedDimension] = Field(default_factory=list)

    validator_report_sha256: str = Field(pattern=Sha256Pattern)

    next_stage: Literal["3.5_multi_variant_experiment_planner"] = "3.5_multi_variant_experiment_planner"

    @model_validator(mode="after")
    def _no_design_blocking_in_handoff(self):
        for u in self.experiment_resolvable_dimensions:
            if u.classification == ResolutionClass.DESIGN_BLOCKING:
                raise ValueError(
                    f"design_blocking unresolved dimension {u.dimension} "
                    f"for variant {u.variant_id} cannot enter 3.5 handoff"
                )
        return self

    @model_validator(mode="after")
    def _nonblocking_warnings_have_correct_classification(self):
        for u in self.nonblocking_warnings:
            if u.classification != ResolutionClass.NONBLOCKING_WARNING:
                raise ValueError(
                    f"nonblocking_warnings must have classification=nonblocking_warning, "
                    f"got {u.classification} for variant {u.variant_id}"
                )
        return self
