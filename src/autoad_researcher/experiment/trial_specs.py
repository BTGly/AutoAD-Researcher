"""Experiment trial-specification builder."""

from autoad_researcher.schemas.experiment_planning import (
    ArtifactRequirement,
    ExperimentPlanningInput,
    ExperimentVariantInput,
    ExperimentTrialSpecs,
    PreparationPhase,
    TrialIntent,
    VariantTrialSpec,
)


def build_trial_specs(
    planning_input: ExperimentPlanningInput,
    protocol_fingerprint: str,
    baseline_intent: TrialIntent | None = None,
    specs_id: str = "",
) -> ExperimentTrialSpecs:
    """Build ExperimentTrialSpecs from confirmed planning input.

    Each variant gets fit/smoke/full intents based on preparation_phase.
    """

    variant_specs = []
    for variant in planning_input.variants:
        prep = variant.preparation_phase

        fit_intent = None
        fit_seed_policy = None
        if prep in {PreparationPhase.FIT, PreparationPhase.TRAIN}:
            fit_intent = _build_fit_intent(variant)
            fit_seed_policy = "shared_fixed"

        smoke = _build_smoke_intent(variant, prep)
        full = _build_full_intent(variant, prep)

        variant_specs.append(VariantTrialSpec(
            variant_id=variant.variant_id,
            variant_label=variant.variant_label,
            idea_id=variant.idea_id,
            primary_hook_id=variant.primary_hook_id,
            hook_bindings=variant.hook_bindings,
            interface_deltas=variant.interface_deltas,
            regime_changes=variant.regime_changes,
            state_changes=variant.state_changes,
            adapter_required=variant.adapter_required,
            new_dependencies=variant.new_dependencies,
            risk_level=variant.risk_level,
            preparation_phase=prep,
            fit=fit_intent,
            fit_seed_policy=fit_seed_policy,
            smoke=smoke,
            full=full,
            implementation_requirements={
                "dependency_deltas": [],
                "asset_requirements": [],
                "accelerator_requirements": {
                    "gpu_required": True,
                    "min_vram_gb": None,
                    "gpu_type_preference": None,
                },
                "environment_rebuild_required": False,
            },
            hyperparameter_plan={
                "mode": "fixed_from_source",
                "source_evidence_ids": variant.evidence_ids,
            },
            evidence_ids=[],
        ))

    return ExperimentTrialSpecs(
        specs_id=specs_id or _gen_id(),
        schema_version=1,
        protocol_fingerprint=protocol_fingerprint,
        baseline=baseline_intent or _build_baseline_intent(),
        variants=variant_specs,
    )


def _build_baseline_intent() -> TrialIntent:
    return TrialIntent(
        intent_id="baseline_eval",
        intent_type="baseline_run",
        description="Run baseline evaluation",
        required_inputs=[],
        expected_outputs=[
            ArtifactRequirement(
                requirement_id="baseline_metrics",
                artifact_type="metrics_json",
                description="Baseline evaluation metrics",
            ),
        ],
    )


def _build_fit_intent(variant: ExperimentVariantInput) -> TrialIntent:
    vid = variant.variant_id
    return TrialIntent(
        intent_id=f"fit_{vid}",
        intent_type="variant_fit",
        description=f"Fit variant {vid}",
        required_inputs=[],
        expected_outputs=[
            ArtifactRequirement(
                requirement_id=f"best_model_{vid}",
                artifact_type="model_weights",
                description=f"Fitted weights for {vid}",
            ),
        ],
    )


def _build_smoke_intent(variant: ExperimentVariantInput, prep: PreparationPhase) -> TrialIntent:
    vid = variant.variant_id
    reqs = []
    if prep in {PreparationPhase.FIT, PreparationPhase.TRAIN}:
        reqs.append(ArtifactRequirement(
            requirement_id=f"model_weights_req_{vid}",
            artifact_type="model_weights",
            description=f"Model weights from fit for {vid}",
        ))
    return TrialIntent(
        intent_id=f"smoke_{vid}",
        intent_type="smoke_inference",
        description=f"Smoke test variant {vid}",
        required_inputs=reqs,
        expected_outputs=[
            ArtifactRequirement(
                requirement_id=f"smoke_metrics_{vid}",
                artifact_type="metrics_json",
                description=f"Smoke test output for {vid}",
            ),
        ],
    )


def _build_full_intent(variant: ExperimentVariantInput, prep: PreparationPhase) -> TrialIntent:
    vid = variant.variant_id
    reqs = []
    if prep in {PreparationPhase.FIT, PreparationPhase.TRAIN}:
        reqs.append(ArtifactRequirement(
            requirement_id=f"model_weights_req_{vid}",
            artifact_type="model_weights",
            description=f"Model weights from fit for {vid}",
        ))
    return TrialIntent(
        intent_id=f"full_{vid}",
        intent_type="full_evaluation",
        description=f"Full evaluation of variant {vid}",
        required_inputs=reqs,
        expected_outputs=[
            ArtifactRequirement(
                requirement_id=f"metrics_{vid}",
                artifact_type="metrics_json",
                description=f"Evaluation metrics for {vid}",
            ),
        ],
    )


def _gen_id() -> str:
    import uuid

    return f"ts_{uuid.uuid4().hex[:8]}"
