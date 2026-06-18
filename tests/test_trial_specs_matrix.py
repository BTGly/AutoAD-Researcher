"""Tests for trial_specs.py + matrix_builder.py — Step 3+4."""

import pytest

from autoad_researcher.experiment.adapter_34 import Stage34InputAdapter
from autoad_researcher.experiment.matrix_builder import MatrixBuildError, build_matrix, _derive_productions
from autoad_researcher.experiment.trial_specs import build_trial_specs
from autoad_researcher.schemas.experiment_planning import (
    PreparationPhase,
    TrialIntent,
)
from autoad_researcher.schemas.transfer_design import (
    DerivedClaim,
    IdeaContract,
    IdeaTransferDesignHandoff,
    IdeaTransferAnalysis,
    ImplementationVariant,
    UserProvidedIdeaContract,
    VariantRiskReport,
    VariantTransferAnalysis,
    TransferStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_variant(variant_id="var_A"):
    return ImplementationVariant(
        variant_id=variant_id,
        variant_label=f"Variant {variant_id}",
        idea_id="idea_001",
        primary_hook_id="hook_01",
        hook_bindings=[],
        interface_deltas=[],
        regime_changes=[],
        state_changes=[],
        adapter_required=False,
        new_dependencies=[],
        expected_behavior_rationale="Better",
        risk_level="low",
        fallback_behavior="Fallback",
    )


def _mock_handoff(variants=None):
    if variants is None:
        variants = [_mock_variant("var_A")]
    return IdeaTransferDesignHandoff(
        run_id="run_001",
        source_context_id="ctx",
        source_context_version=1,
        source_context_sha256="a" * 64,
        confirmed_idea=IdeaContract(
            idea_id="idea_001",
            idea_source=UserProvidedIdeaContract(
                user_description="Idea",
                mechanism_hypothesis=DerivedClaim(value="test"),
                transfer_relevance=DerivedClaim(value="relevant"),
            ),
            confirmation_status="pending",
        ),
        idea_contract_sha256="b" * 64,
        transfer_analysis=IdeaTransferAnalysis(
            idea_id="idea_001",
            variant_analyses={
                v.variant_id: VariantTransferAnalysis(
                    variant_id=v.variant_id,
                    overall_status=TransferStatus.VIABLE,
                )
                for v in variants
            },
        ),
        transfer_constraints=[],
        selected_variants=variants,
        variant_selection_sha256="c" * 64,
        variant_risk_reports=[
            VariantRiskReport(variant_id=v.variant_id, computed_risk_level="low")
            for v in variants
        ],
        validator_report_sha256="d" * 64,
    )


def _mock_protocol():
    from autoad_researcher.schemas.experiment_planning import (
        BaselineExecutionPolicy,
        PlanningInputRefs,
        SharedExperimentProtocol,
        SupplementalEvaluationRefs,
    )
    from autoad_researcher.schemas.artifacts import ArtifactReferenceV2

    return SharedExperimentProtocol(
        protocol_id="proto_test",
        schema_version=1,
        planning_input_refs=PlanningInputRefs(
            repository_fingerprint="rf",
            environment_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64,
            asset_manifest_sha256="c" * 64,
        ),
        supplemental_refs=SupplementalEvaluationRefs(),
        evaluation_protocol_ref=ArtifactReferenceV2(
            artifact_id="eval", artifact_type="config", locator="x", sha256="a" * 64,
        ),
        baseline_method="patchcore",
        baseline_config_sha256="g" * 64,
        baseline_policy=BaselineExecutionPolicy(mode="run_fresh", seeds=[42, 43]),
        seeds=[42, 43],
        primary_metric="auroc",
        metric_direction="maximize",
        protected_paths=[],
        must_not_change=[],
        protocol_evidence_ids=[],
        protocol_fingerprint="fp_test",
    )


# ---------------------------------------------------------------------------
# trial_specs tests
# ---------------------------------------------------------------------------

def test_build_trial_specs_no_regime_changes():
    adapter = Stage34InputAdapter()
    input_ = adapter.load(_mock_handoff([_mock_variant("var_A")]))
    specs = build_trial_specs(input_, "fp_test")
    assert len(specs.variants) == 1
    v = specs.variants[0]
    assert v.preparation_phase == PreparationPhase.NONE
    assert v.fit is None
    assert v.fit_seed_policy is None
    assert v.smoke is not None
    assert v.full is not None


def test_build_trial_specs_duplicate_variant_rejected():
    adapter = Stage34InputAdapter()
    input_ = adapter.load(_mock_handoff([_mock_variant("var_A")]))
    from pydantic import ValidationError

    # Build one variant spec twice
    specs = build_trial_specs(input_, "fp_test")
    specs.variants.append(specs.variants[0])
    with pytest.raises(ValidationError, match="duplicate"):
        specs.model_validate(specs.model_dump())


# ---------------------------------------------------------------------------
# matrix_builder tests
# ---------------------------------------------------------------------------

def test_build_matrix_baseline_entries():
    adapter = Stage34InputAdapter()
    input_ = adapter.load(_mock_handoff([_mock_variant("var_A")]))
    specs = build_trial_specs(input_, "fp_test")
    protocol = _mock_protocol()

    matrix = build_matrix(protocol, specs)
    baseline_entries = [e for e in matrix.entries if e.stage == "baseline"]
    assert len(baseline_entries) == 2  # seeds [42, 43]


def test_build_matrix_smoke_full_chain():
    adapter = Stage34InputAdapter()
    input_ = adapter.load(_mock_handoff([_mock_variant("var_A")]))
    specs = build_trial_specs(input_, "fp_test")
    protocol = _mock_protocol()

    matrix = build_matrix(protocol, specs)
    entries_by_stage = {}
    for e in matrix.entries:
        entries_by_stage.setdefault(e.stage, []).append(e.entry_id)

    assert "smoke" in entries_by_stage
    assert "full" in entries_by_stage
    assert len(entries_by_stage["full"]) == 2  # 2 seeds


def test_build_matrix_with_fit():
    from autoad_researcher.schemas.transfer_design import (
        ExecutionPhaseContract,
        RegimeChange,
    )

    v = _mock_variant("var_B")
    v.regime_changes = [
        RegimeChange(
            phase_id="p1",
            before_phase=None,
            after_phase=ExecutionPhaseContract(
                phase_id="phase_fit", phase="fit",
            ),
            gradient_required=True,
        ),
    ]
    adapter = Stage34InputAdapter()
    input_ = adapter.load(_mock_handoff([v]))
    specs = build_trial_specs(input_, "fp_test")
    protocol = _mock_protocol()

    matrix = build_matrix(protocol, specs)
    fit_entries = [e for e in matrix.entries if e.stage == "fit"]
    assert len(fit_entries) == 1  # shared_fixed


def test_build_matrix_bindings():
    from autoad_researcher.schemas.transfer_design import (
        ExecutionPhaseContract,
        RegimeChange,
    )

    v = _mock_variant("var_B")
    v.regime_changes = [
        RegimeChange(
            phase_id="p1",
            before_phase=None,
            after_phase=ExecutionPhaseContract(phase_id="p_fit", phase="fit"),
            gradient_required=True,
        ),
    ]
    adapter = Stage34InputAdapter()
    input_ = adapter.load(_mock_handoff([v]))
    specs = build_trial_specs(input_, "fp_test")
    protocol = _mock_protocol()

    matrix = build_matrix(protocol, specs)
    assert len(matrix.input_bindings) >= 2  # smoke + full for each seed


def test_derive_productions():
    from autoad_researcher.schemas.experiment_planning import ArtifactRequirement

    intent = TrialIntent(
        intent_id="fit_x",
        intent_type="variant_fit",
        description="Fit",
        required_inputs=[],
        expected_outputs=[
            ArtifactRequirement(
                requirement_id="best_model_x",
                artifact_type="model_weights",
                description="Weights",
            ),
        ],
    )
    prods = _derive_productions(intent)
    assert len(prods) == 1
    assert prods[0].production_id == "best_model_x"
