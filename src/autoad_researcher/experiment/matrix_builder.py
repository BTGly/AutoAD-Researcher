"""Step 4: ExperimentMatrix builder."""

from autoad_researcher.schemas.experiment_planning import (
    ArtifactProduction,
    ArtifactRequirement,
    ExperimentMatrix,
    ExperimentTrialSpecs,
    MatrixEntry,
    MatrixInputBinding,
    PreparationPhase,
    SharedExperimentProtocol,
    TrialIntent,
    VariantTrialSpec,
)


class MatrixBuildError(Exception):
    """Raised when matrix cannot be built from given specs."""


def build_matrix(
    protocol: SharedExperimentProtocol,
    specs: ExperimentTrialSpecs,
) -> ExperimentMatrix:
    """Build ExperimentMatrix with entries + bindings from protocol and specs."""

    entries: list[MatrixEntry] = []
    seeds = protocol.seeds
    baseline_policy = protocol.baseline_policy

    # 1. Baseline entries
    if baseline_policy.mode == "run_fresh":
        assert specs.baseline is not None, "baseline intent required for run_fresh"
        for seed in seeds:
            entries.append(MatrixEntry(
                entry_id=f"baseline_s{seed}",
                variant_id=None,
                stage="baseline",
                seed=seed,
                intent_ref=specs.baseline.intent_id,
                depends_on=[],
                shared_axes=["protocol", "dataset", "metric"],
                independent_axes=[f"seed_{seed}"],
                priority=0,
            ))

    # 2. Variant entries
    for variant in specs.variants:
        fit_entries: list[MatrixEntry] = []
        fit_policy = variant.fit_seed_policy if variant.fit is not None else None

        # 2a. Fit (if needed)
        if variant.fit is not None:
            if fit_policy == "shared_fixed":
                fit_seed = seeds[0]
                fit_id = f"{variant.variant_id}_fit_s{fit_seed}"
                fit_entries.append(MatrixEntry(
                    entry_id=fit_id,
                    variant_id=variant.variant_id,
                    stage="fit",
                    seed=fit_seed,
                    intent_ref=variant.fit.intent_id,
                    depends_on=[],
                    shared_axes=["protocol", "dataset", "baseline"],
                    independent_axes=["variant_modification", "fit_stage", "fit_seed_shared"],
                    expected_outputs=_derive_productions(variant.fit),
                    priority=0,
                ))
            elif fit_policy == "per_evaluation_seed":
                for seed in seeds:
                    fit_id = f"{variant.variant_id}_fit_s{seed}"
                    fit_entries.append(MatrixEntry(
                        entry_id=fit_id,
                        variant_id=variant.variant_id,
                        stage="fit",
                        seed=seed,
                        intent_ref=variant.fit.intent_id,
                        depends_on=[],
                        shared_axes=["protocol", "dataset", "baseline"],
                        independent_axes=["variant_modification", "fit_stage", f"seed_{seed}"],
                        expected_outputs=_derive_productions(variant.fit),
                        priority=0,
                    ))
            elif fit_policy == "deterministic_no_seed":
                fit_id = f"{variant.variant_id}_fit_det"
                fit_entries.append(MatrixEntry(
                    entry_id=fit_id,
                    variant_id=variant.variant_id,
                    stage="fit",
                    seed=None,
                    intent_ref=variant.fit.intent_id,
                    depends_on=[],
                    shared_axes=["protocol", "dataset", "baseline"],
                    independent_axes=["variant_modification", "fit_stage", "fit_deterministic"],
                    expected_outputs=_derive_productions(variant.fit),
                    priority=0,
                ))
            entries.extend(fit_entries)

        # 2b. Smoke
        smoke_entry_ids: list[str] = []
        if fit_policy == "per_evaluation_seed":
            for seed in seeds:
                sid = f"{variant.variant_id}_smoke_s{seed}"
                smoke_entry_ids.append(sid)
                entries.append(MatrixEntry(
                    entry_id=sid,
                    variant_id=variant.variant_id,
                    stage="smoke",
                    seed=seed,
                    intent_ref=variant.smoke.intent_id,
                    depends_on=[f"{variant.variant_id}_fit_s{seed}"],
                    shared_axes=["protocol", "dataset", "metric", "baseline"],
                    independent_axes=["variant_modification", "smoke_check", f"seed_{seed}"],
                    priority=1,
                ))
        else:
            smoke_id = f"{variant.variant_id}_smoke"
            smoke_deps = [e.entry_id for e in fit_entries] if fit_entries else []
            entries.append(MatrixEntry(
                entry_id=smoke_id,
                variant_id=variant.variant_id,
                stage="smoke",
                seed=seeds[0],
                intent_ref=variant.smoke.intent_id,
                depends_on=smoke_deps,
                shared_axes=["protocol", "dataset", "metric", "baseline"],
                independent_axes=["variant_modification", "smoke_check"],
                priority=1,
            ))
            smoke_entry_ids = [smoke_id]

        # 2c. Full
        for seed in seeds:
            full_deps = (
                smoke_entry_ids[:1]
                if fit_policy != "per_evaluation_seed"
                else [f"{variant.variant_id}_smoke_s{seed}"]
            )
            if fit_policy == "per_evaluation_seed" and fit_entries:
                full_deps.append(f"{variant.variant_id}_fit_s{seed}")
            elif fit_policy == "shared_fixed" and fit_entries:
                full_deps.append(f"{variant.variant_id}_fit_s{seeds[0]}")
            elif fit_policy == "deterministic_no_seed" and fit_entries:
                full_deps.append(f"{variant.variant_id}_fit_det")

            entries.append(MatrixEntry(
                entry_id=f"{variant.variant_id}_full_s{seed}",
                variant_id=variant.variant_id,
                stage="full",
                seed=seed,
                intent_ref=variant.full.intent_id,
                depends_on=full_deps,
                shared_axes=["protocol", "dataset", "metric", "baseline"],
                independent_axes=["variant_modification", f"seed_{seed}"],
                priority=2,
            ))

    # 3. Input bindings
    bindings = _build_bindings(specs, seeds)

    return ExperimentMatrix(
        matrix_id=f"matrix_{protocol.protocol_id}",
        schema_version=1,
        protocol_fingerprint=protocol.protocol_fingerprint,
        seeds=list(seeds),
        variants=[v.variant_id for v in specs.variants],
        entries=entries,
        input_bindings=bindings,
    )


def _derive_productions(intent: TrialIntent) -> list[ArtifactProduction]:
    """Derive ArtifactProduction from TrialIntent.expected_outputs."""
    return [
        ArtifactProduction(
            production_id=r.requirement_id,
            artifact_type=r.artifact_type if r.artifact_type != "model_weights" else "model_weights",
            description=r.description,
        )
        for r in intent.expected_outputs
    ]


def _build_bindings(
    specs: ExperimentTrialSpecs,
    seeds: list[int],
) -> list[MatrixInputBinding]:
    bindings: list[MatrixInputBinding] = []

    for variant in specs.variants:
        if variant.fit is None:
            continue

        smoke_weight_req = _require_input(variant.smoke, "model_weights", variant.variant_id, "smoke")
        full_weight_req = _require_input(variant.full, "model_weights", variant.variant_id, "full")

        weight_prod = next(
            (r for r in variant.fit.expected_outputs if r.artifact_type == "model_weights"),
            None,
        )
        if weight_prod is None:
            raise MatrixBuildError(
                f"variant {variant.variant_id}: fit intent must declare "
                "a model_weights output"
            )
        weight_production_id = weight_prod.requirement_id

        fit_policy = variant.fit_seed_policy or "shared_fixed"

        # Smoke bindings
        if fit_policy == "per_evaluation_seed":
            for seed in seeds:
                bindings.append(MatrixInputBinding(
                    consumer_entry_id=f"{variant.variant_id}_smoke_s{seed}",
                    consumer_requirement_id=smoke_weight_req.requirement_id,
                    producer_entry_id=f"{variant.variant_id}_fit_s{seed}",
                    producer_production_id=weight_production_id,
                ))
        else:
            smoke_id = f"{variant.variant_id}_smoke"
            fit_id = (
                f"{variant.variant_id}_fit_s{seeds[0]}"
                if fit_policy == "shared_fixed"
                else f"{variant.variant_id}_fit_det"
            )
            bindings.append(MatrixInputBinding(
                consumer_entry_id=smoke_id,
                consumer_requirement_id=smoke_weight_req.requirement_id,
                producer_entry_id=fit_id,
                producer_production_id=weight_production_id,
            ))

        # Full bindings
        for full_seed in seeds:
            full_entry = f"{variant.variant_id}_full_s{full_seed}"
            if fit_policy == "per_evaluation_seed":
                fit_entry = f"{variant.variant_id}_fit_s{full_seed}"
            elif fit_policy == "shared_fixed":
                fit_entry = f"{variant.variant_id}_fit_s{seeds[0]}"
            else:
                fit_entry = f"{variant.variant_id}_fit_det"
            bindings.append(MatrixInputBinding(
                consumer_entry_id=full_entry,
                consumer_requirement_id=full_weight_req.requirement_id,
                producer_entry_id=fit_entry,
                producer_production_id=weight_production_id,
            ))

    return bindings


def _require_input(
    intent: TrialIntent,
    artifact_type: str,
    variant_id: str,
    intent_name: str,
) -> ArtifactRequirement:
    req = next(
        (r for r in intent.required_inputs if r.artifact_type == artifact_type),
        None,
    )
    if req is None:
        raise MatrixBuildError(
            f"variant {variant_id}: {intent_name} intent must declare "
            f"a {artifact_type} requirement when fit exists"
        )
    return req
