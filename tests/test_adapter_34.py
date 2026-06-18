"""Tests for Stage34InputAdapter (adapter_34.py)."""

import pytest

from autoad_researcher.experiment.adapter_34 import (
    Stage34HandoffError,
    Stage34InputAdapter,
    compute_unresolved_dimension_id,
    derive_preparation_phase,
)
from autoad_researcher.schemas.experiment_planning import PreparationPhase
from autoad_researcher.schemas.transfer_design import (
    CompatibilityDimension,
    CompatibilityStatus,
    ConstraintRef,
    DerivedClaim,
    DimensionJudgment,
    ExecutionPhaseContract,
    IdeaContract,
    IdeaTransferAnalysis,
    IdeaTransferDesignHandoff,
    ImplementationVariant,
    RegimeChange,
    ResolutionClass,
    TransferStatus,
    UnresolvedDimension,
    UserProvidedIdeaContract,
    VariantRiskReport,
    VariantTransferAnalysis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_phase(phase="infer"):
    return ExecutionPhaseContract(
        phase_id=f"phase_{phase}",
        phase=phase,
    )


def _mock_variant(variant_id="var_A", regime_changes=None):
    return ImplementationVariant(
        variant_id=variant_id,
        variant_label=f"Variant {variant_id}",
        idea_id="idea_001",
        primary_hook_id="hook_01",
        hook_bindings=[],
        interface_deltas=[],
        regime_changes=regime_changes or [],
        state_changes=[],
        adapter_required=False,
        new_dependencies=[],
        expected_behavior_rationale="Better",
        risk_level="low",
        fallback_behavior="Fallback",
    )


def _mock_analysis(variant_id="var_A", overall_status="viable"):
    return VariantTransferAnalysis(
        variant_id=variant_id,
        dimensions=[],
        overall_status=overall_status if isinstance(overall_status, TransferStatus) else TransferStatus(overall_status),
        constraints=[],
        unresolved_dimensions=[],
    )


def _mock_risk(variant_id="var_A"):
    return VariantRiskReport(
        variant_id=variant_id,
        computed_risk_level="low",
        records=[],
        accepted_risks=[],
    )


def _mock_idea():
    return IdeaContract(
        idea_id="idea_001",
        idea_source=UserProvidedIdeaContract(
            user_description="Test idea description",
            mechanism_hypothesis=DerivedClaim(value="test"),
            transfer_relevance=DerivedClaim(value="relevant"),
        ),
        must_preserve_behaviors=[],
        confirmation_status="pending",
    )


def _mock_handoff(
    selected_variants=None,
    analyses=None,
    risks=None,
    experiment_resolvable=None,
):
    if selected_variants is None:
        selected_variants = [_mock_variant("var_A")]
    if analyses is None:
        analyses = {"var_A": _mock_analysis("var_A")}
    if risks is None:
        risks = [_mock_risk("var_A")]

    return IdeaTransferDesignHandoff(
        run_id="run_001",
        source_context_id="ctx_001",
        source_context_version=1,
        source_context_sha256="a" * 64,
        confirmed_idea=_mock_idea(),
        idea_contract_sha256="b" * 64,
        transfer_analysis=IdeaTransferAnalysis(
            idea_id="idea_001",
            variant_analyses=analyses,
        ),
        transfer_constraints=[],
        selected_variants=selected_variants,
        variant_selection_sha256="c" * 64,
        variant_risk_reports=risks,
        experiment_resolvable_dimensions=experiment_resolvable or [],
        nonblocking_warnings=[],
        validator_report_sha256="d" * 64,
    )


# ---------------------------------------------------------------------------
# Fail-closed — empty selected_variants
# ---------------------------------------------------------------------------

def test_adapter_empty_selected_variants():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff(selected_variants=[])
    with pytest.raises(Stage34HandoffError, match="selected_variants must not be empty"):
        adapter.load(handoff)


# ---------------------------------------------------------------------------
# Fail-closed — missing analysis
# ---------------------------------------------------------------------------

def test_adapter_missing_analysis():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff(
        selected_variants=[_mock_variant("var_A")],
        analyses={},
    )
    with pytest.raises(Stage34HandoffError, match="has no VariantTransferAnalysis"):
        adapter.load(handoff)


# ---------------------------------------------------------------------------
# Fail-closed — analysis variant_id mismatch
# ---------------------------------------------------------------------------

def test_adapter_analysis_variant_id_mismatch():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff(
        selected_variants=[_mock_variant("var_A")],
        analyses={"var_A": _mock_analysis("var_B")},
    )
    with pytest.raises(Stage34HandoffError, match="analysis.variant_id"):
        adapter.load(handoff)


# ---------------------------------------------------------------------------
# Fail-closed — non-viable status
# ---------------------------------------------------------------------------

def test_adapter_non_viable_status():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff(
        selected_variants=[_mock_variant("var_A")],
        analyses={"var_A": _mock_analysis("var_A", "non_viable")},
    )
    with pytest.raises(Stage34HandoffError, match="non-viable status"):
        adapter.load(handoff)


def test_adapter_needs_reanalysis_status():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff(
        selected_variants=[_mock_variant("var_A")],
        analyses={"var_A": _mock_analysis("var_A", "needs_reanalysis")},
    )
    with pytest.raises(Stage34HandoffError, match="non-viable status"):
        adapter.load(handoff)


# ---------------------------------------------------------------------------
# Fail-closed — missing risk report
# ---------------------------------------------------------------------------

def test_adapter_missing_risk():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff(
        selected_variants=[_mock_variant("var_A")],
        risks=[],
    )
    with pytest.raises(Stage34HandoffError, match="has no VariantRiskReport"):
        adapter.load(handoff)


# ---------------------------------------------------------------------------
# Fail-closed — duplicate risk variant_id
# ---------------------------------------------------------------------------

def test_adapter_duplicate_risk():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff(
        selected_variants=[_mock_variant("var_A")],
        risks=[_mock_risk("var_A"), _mock_risk("var_A")],
    )
    with pytest.raises(Stage34HandoffError, match="duplicate"):
        adapter.load(handoff)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_adapter_happy_path_single_variant():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff()
    result = adapter.load(handoff)
    assert result.run_id == "run_001"
    assert len(result.variants) == 1
    assert result.variants[0].variant.variant_id == "var_A"


def test_adapter_happy_path_multiple_variants():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff(
        selected_variants=[_mock_variant("var_A"), _mock_variant("var_B")],
        analyses={
            "var_A": _mock_analysis("var_A"),
            "var_B": _mock_analysis("var_B"),
        },
        risks=[_mock_risk("var_A"), _mock_risk("var_B")],
    )
    result = adapter.load(handoff)
    assert len(result.variants) == 2
    assert {v.variant.variant_id for v in result.variants} == {"var_A", "var_B"}


def test_adapter_passes_transfer_constraints():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff()
    result = adapter.load(handoff)
    assert result.transfer_constraints == []


def test_adapter_passes_nonblocking_warnings():
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff()
    result = adapter.load(handoff)
    assert result.nonblocking_warnings == []


def test_adapter_filters_experiment_resolvable_by_variant():
    u1 = UnresolvedDimension(
        variant_id="var_A",
        dimension=CompatibilityDimension.TRAINING,
        status=CompatibilityStatus.REQUIRES_REGIME_CHANGE,
        classification=ResolutionClass.EXPERIMENT_RESOLVABLE,
        resolution_reason="Needs fit",
        verification_target="loss",
        acceptance_criterion="< 0.5",
        evidence_ids=[],
        classified_by_rule_id="rule_01",
    )
    u2 = UnresolvedDimension(
        variant_id="var_B",
        dimension=CompatibilityDimension.SHAPE,
        status=CompatibilityStatus.REQUIRES_REGIME_CHANGE,
        classification=ResolutionClass.EXPERIMENT_RESOLVABLE,
        resolution_reason="Shape mismatch",
        verification_target="output_shape",
        acceptance_criterion="matches baseline",
        evidence_ids=[],
        classified_by_rule_id="rule_02",
    )
    adapter = Stage34InputAdapter()
    handoff = _mock_handoff(
        selected_variants=[_mock_variant("var_A"), _mock_variant("var_B")],
        analyses={
            "var_A": _mock_analysis("var_A"),
            "var_B": _mock_analysis("var_B"),
        },
        risks=[_mock_risk("var_A"), _mock_risk("var_B")],
        experiment_resolvable=[u1, u2],
    )
    result = adapter.load(handoff)
    assert len(result.variants[0].experiment_resolvable) == 1
    assert result.variants[0].experiment_resolvable[0].variant_id == "var_A"
    assert len(result.variants[1].experiment_resolvable) == 1
    assert result.variants[1].experiment_resolvable[0].variant_id == "var_B"


# ---------------------------------------------------------------------------
# derive_preparation_phase
# ---------------------------------------------------------------------------

def test_derive_preparation_phase_gradient():
    v = _mock_variant(regime_changes=[
        RegimeChange(
            phase_id="p1",
            before_phase=_mock_phase("infer"),
            after_phase=_mock_phase("fit"),
            gradient_required=True,
        ),
    ])
    assert derive_preparation_phase(v) == PreparationPhase.FIT


def test_derive_preparation_phase_training_phase():
    v = _mock_variant(regime_changes=[
        RegimeChange(
            phase_id="p1",
            before_phase=None,
            after_phase=_mock_phase("train"),
            gradient_required=False,
        ),
    ])
    assert derive_preparation_phase(v) == PreparationPhase.TRAIN


def test_derive_preparation_phase_state_mutation_infer():
    v = _mock_variant(regime_changes=[
        RegimeChange(
            phase_id="p1",
            before_phase=_mock_phase("infer"),
            after_phase=_mock_phase("infer"),
            gradient_required=False,
            state_mutation_required=True,
        ),
    ])
    assert derive_preparation_phase(v) == PreparationPhase.INFER_INIT


def test_derive_preparation_phase_state_mutation_postprocess():
    v = _mock_variant(regime_changes=[
        RegimeChange(
            phase_id="p1",
            before_phase=None,
            after_phase=_mock_phase("postprocess"),
            gradient_required=False,
            state_mutation_required=True,
        ),
    ])
    assert derive_preparation_phase(v) == PreparationPhase.ONLINE_STATE


def test_derive_preparation_phase_none():
    v = _mock_variant(regime_changes=[
        RegimeChange(
            phase_id="p1",
            before_phase=_mock_phase("infer"),
            after_phase=_mock_phase("evaluate"),
            gradient_required=False,
            state_mutation_required=False,
        ),
    ])
    assert derive_preparation_phase(v) == PreparationPhase.NONE


def test_derive_preparation_phase_no_changes():
    v = _mock_variant()
    assert derive_preparation_phase(v) == PreparationPhase.NONE


# ---------------------------------------------------------------------------
# compute_unresolved_dimension_id — deterministic hash
# ---------------------------------------------------------------------------

def test_compute_unresolved_dimension_id_deterministic():
    id1 = compute_unresolved_dimension_id("var_A", "training", "loss")
    id2 = compute_unresolved_dimension_id("var_A", "training", "loss")
    assert id1 == id2
    assert len(id1) == 64


def test_compute_unresolved_dimension_id_changes_with_input():
    id1 = compute_unresolved_dimension_id("var_A", "training", "loss")
    id2 = compute_unresolved_dimension_id("var_A", "shape", "loss")
    assert id1 != id2
