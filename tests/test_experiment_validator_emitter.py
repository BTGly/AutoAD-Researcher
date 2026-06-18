"""Regression tests for Step 3.5 PlanValidator + HandoffEmitter."""

import json
from pathlib import Path

from autoad_researcher.experiment.validator_emitter import (
    emit_handoff,
    validate_plan,
)
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.experiment_planning import (
    PLANNING_ARTIFACT_PATHS,
    AllSeedsDegradedCondition,
    AllSeedsImprovedCondition,
    AlwaysCondition,
    ArtifactProduction,
    ArtifactRequirement,
    BaselineExecutionPolicy,
    BudgetDecision,
    EntryResourceEstimate,
    ExperimentBundleResourceBudget,
    ExperimentMatrix,
    ExperimentTrialSpecs,
    ExperimentalResolutionPlan,
    ExperimentalResolutionPlans,
    IncompletePairsCondition,
    InterfaceConstraint,
    MatrixEntry,
    MatrixInputBinding,
    MeanImprovedAboveThresholdCondition,
    OperationalGuard,
    OperationalGuardPolicy,
    PlanningInputRefs,
    PreparationPhase,
    RangeCriterion,
    ResourceBudget,
    ResourceLimits,
    ResolutionOutcome,
    ScientificConclusion,
    ScientificDecisionRule,
    SharedExperimentProtocol,
    StatisticalAnalysisPlan,
    SupplementalEvaluationRefs,
    TrialIntent,
    VariantTrialSpec,
    WithinEquivalenceMarginCondition,
)


def test_validate_plan_invalid_json_is_blocking_structure_issue(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    artifacts["shared_experiment_protocol.json"].write_text("{not json")

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(
        i.issue_id == "struct_invalid_shared_experiment_protocol_json"
        for i in report.issues
    )
    structure = next(r for r in report.invariant_results if r.category == "structure")
    assert not structure.passed


def test_validate_plan_missing_artifact_returns_failed_report(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    artifacts["resource_budget.json"].unlink()

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(i.issue_id == "struct_missing_resource_budget.json" for i in report.issues)
    assert {
        ref.relative_path for ref in report.validated_artifact_refs
    } == set(PLANNING_ARTIFACT_PATHS) - {"resource_budget.json"}


def test_emit_handoff_manifest_includes_report_without_self_binding(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    report = validate_plan(artifacts, run_id="run_validator")
    assert report.status == "passed"
    artifacts["experiment_plan_validation_report.json"] = (
        tmp_path / "experiment_plan_validation_report.json"
    )
    _write_model(artifacts["experiment_plan_validation_report.json"], report)

    handoff = emit_handoff(
        artifacts,
        run_id="run_validator",
        source_sha256="a" * 64,
    )

    manifest_paths = {e.relative_path for e in handoff.artifact_manifest.entries}
    assert manifest_paths == {
        *PLANNING_ARTIFACT_PATHS,
        "experiment_plan_validation_report.json",
    }
    report_refs = {r.relative_path for r in report.validated_artifact_refs}
    assert report_refs == set(PLANNING_ARTIFACT_PATHS)


def test_validate_plan_rejects_binding_to_missing_requirement(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    matrix_data = json.loads(artifacts["experiment_matrix.json"].read_text())
    matrix_data["input_bindings"][0]["consumer_requirement_id"] = "missing_req"
    artifacts["experiment_matrix.json"].write_text(json.dumps(matrix_data))

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(i.issue_id == "dag_binding_requirement_var_A_smoke" for i in report.issues)


def test_validate_plan_rejects_missing_smoke_binding(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    matrix_data = json.loads(artifacts["experiment_matrix.json"].read_text())
    matrix_data["input_bindings"] = matrix_data["input_bindings"][1:]
    artifacts["experiment_matrix.json"].write_text(json.dumps(matrix_data))

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(
        i.issue_id == "dag_binding_missing_var_A_smoke_model_weights_req_var_A"
        for i in report.issues
    )


def test_validate_plan_rejects_missing_full_binding(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    matrix_data = json.loads(artifacts["experiment_matrix.json"].read_text())
    matrix_data["input_bindings"] = matrix_data["input_bindings"][:1]
    artifacts["experiment_matrix.json"].write_text(json.dumps(matrix_data))

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(
        i.issue_id == "dag_binding_missing_var_A_full_s42_model_weights_req_var_A"
        for i in report.issues
    )


def test_validate_plan_rejects_duplicate_requirement_binding(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    matrix_data = json.loads(artifacts["experiment_matrix.json"].read_text())
    matrix_data["input_bindings"].append(matrix_data["input_bindings"][0])
    artifacts["experiment_matrix.json"].write_text(json.dumps(matrix_data))

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(
        i.issue_id == "dag_binding_duplicate_var_A_smoke_model_weights_req_var_A"
        for i in report.issues
    )


def test_validate_plan_rejects_binding_producer_outside_dependency_chain(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    matrix_data = json.loads(artifacts["experiment_matrix.json"].read_text())
    for entry in matrix_data["entries"]:
        if entry["entry_id"] == "var_A_smoke":
            entry["depends_on"] = []
    artifacts["experiment_matrix.json"].write_text(json.dumps(matrix_data))

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(
        i.issue_id == "dag_binding_not_dependency_var_A_smoke_var_A_fit_s42"
        for i in report.issues
    )


def test_validate_plan_rejects_fixed_from_source_without_evidence(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    specs_data = json.loads(artifacts["experiment_trial_specs.json"].read_text())
    specs_data["variants"][0]["hyperparameter_plan"]["source_evidence_ids"] = []
    artifacts["experiment_trial_specs.json"].write_text(json.dumps(specs_data))

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(
        i.issue_id == "hp_fixed_source_missing_evidence_var_A"
        for i in report.issues
    )


def test_validate_plan_rejects_predeclared_search_missing_fields(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    specs_data = json.loads(artifacts["experiment_trial_specs.json"].read_text())
    specs_data["variants"][0]["hyperparameter_plan"] = {
        "mode": "predeclared_search",
        "source_evidence_ids": ["ev_hp"],
        "selection_split": {
            "partition_id": "val",
            "dataset_manifest_sha256": "b" * 64,
            "declared_role": "validation",
            "evidence_ids": ["ev_split"],
        },
    }
    artifacts["experiment_trial_specs.json"].write_text(json.dumps(specs_data))

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(i.issue_id == "hp_search_missing_var_A" for i in report.issues)


def test_validate_plan_rejects_predeclared_search_budget_mismatch(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    specs_data = json.loads(artifacts["experiment_trial_specs.json"].read_text())
    var_a = specs_data["variants"][0]
    var_a["hyperparameter_plan"] = _predeclared_search_plan(max_trials=5)
    var_b = json.loads(json.dumps(var_a))
    var_b["variant_id"] = "var_B"
    var_b["variant_label"] = "Variant B"
    var_b["idea_id"] = "idea_B"
    var_b["primary_hook_id"] = "hook_B"
    var_b["fit"]["intent_id"] = "fit_var_B"
    var_b["smoke"]["intent_id"] = "smoke_var_B"
    var_b["full"]["intent_id"] = "full_var_B"
    var_b["hyperparameter_plan"] = _predeclared_search_plan(max_trials=8)
    specs_data["variants"].append(var_b)
    artifacts["experiment_trial_specs.json"].write_text(json.dumps(specs_data))

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(i.issue_id == "hp_search_budget_mismatch" for i in report.issues)


def test_validate_plan_rejects_incomplete_budget_entry_coverage(tmp_path):
    artifacts = _write_valid_artifacts(tmp_path)
    budget_data = json.loads(artifacts["resource_budget.json"].read_text())
    budget_data["per_variant"]["var_A"]["entries"] = budget_data["per_variant"]["var_A"]["entries"][:-1]
    budget_data["per_variant"]["var_A"]["total_gpu_hours"] = 2.0
    budget_data["total_estimate"]["total_gpu_hours"] = 3.0
    artifacts["resource_budget.json"].write_text(json.dumps(budget_data))

    report = validate_plan(artifacts, run_id="run_validator")

    assert report.status == "failed"
    assert any(i.issue_id == "budget_missing_entry_estimate" for i in report.issues)


def _predeclared_search_plan(max_trials: int) -> dict:
    return {
        "mode": "predeclared_search",
        "source_evidence_ids": ["ev_hp"],
        "search_space": [
            {"name": "k", "type": "int", "range": [1, 3]},
        ],
        "selection_split": {
            "partition_id": "val",
            "dataset_manifest_sha256": "b" * 64,
            "declared_role": "validation",
            "evidence_ids": ["ev_split"],
        },
        "search_budget": {
            "max_trials": max_trials,
            "max_gpu_hours": 2.0,
        },
        "selection_metric": "image_auroc",
    }


def _write_valid_artifacts(root: Path) -> dict[str, Path]:
    artifacts = _valid_artifact_models()
    paths: dict[str, Path] = {}
    for name, artifact in artifacts.items():
        path = root / name
        _write_model(path, artifact)
        paths[name] = path
    return paths


def _write_model(path: Path, model) -> None:
    path.write_text(model.model_dump_json(indent=2))


def _valid_artifact_models():
    protocol = _protocol()
    specs = _trial_specs()
    matrix = _matrix()
    budget = _budget()
    return {
        "shared_experiment_protocol.json": protocol,
        "statistical_analysis_plan.json": _stat_plan(),
        "experiment_trial_specs.json": specs,
        "experiment_matrix.json": matrix,
        "experimental_resolution_plans.json": _resolution_plans(),
        "resource_budget.json": budget,
        "operational_guard_policy.json": _guard_policy(),
    }


def _protocol() -> SharedExperimentProtocol:
    return SharedExperimentProtocol(
        protocol_id="proto_validator",
        schema_version=1,
        planning_input_refs=PlanningInputRefs(
            repository_fingerprint="repo_fp",
            environment_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64,
            asset_manifest_sha256="c" * 64,
        ),
        supplemental_refs=SupplementalEvaluationRefs(
            evaluator_coverage_evidence_ids=["ev_eval"],
            metric_parser_coverage_evidence_ids=["ev_metric_parser"],
            postprocessing_coverage_evidence_ids=["ev_post"],
            dataset_split_coverage_evidence_ids=["ev_split"],
        ),
        evaluation_protocol_ref=ArtifactReferenceV2(
            artifact_id="eval_proto",
            artifact_type="config",
            locator="runs/run_validator/eval_proto.json",
            sha256="d" * 64,
        ),
        baseline_method="patchcore",
        baseline_config_sha256="e" * 64,
        baseline_policy=BaselineExecutionPolicy(mode="run_fresh", seeds=[42]),
        seeds=[42],
        primary_metric="image_auroc",
        metric_direction="maximize",
        protected_paths=[],
        must_not_change=[
            InterfaceConstraint(
                reason="preserve evaluator",
                contract_description="Evaluator contract must remain unchanged",
            )
        ],
        protocol_evidence_ids=["ev_protocol"],
        protocol_fingerprint="fp_validator",
    )


def _stat_plan() -> StatisticalAnalysisPlan:
    return StatisticalAnalysisPlan(
        plan_id="sp_validator",
        schema_version=1,
        protocol_fingerprint="fp_validator",
        primary_metric="image_auroc",
        metric_direction="maximize",
        aggregation="mean",
        dispersion="std",
        paired_by_seed=True,
        missing_run_policy="report_incomplete",
        max_rerun_attempts=1,
        multiple_variant_policy="descriptive_only",
        decision_rules=[
            ScientificDecisionRule(
                rule_id="rule_incomplete",
                priority=10,
                description="Insufficient completed pairs",
                condition=IncompletePairsCondition(min_pairs=1),
                conclusion_code=ScientificConclusion.INCOMPLETE,
                narrative_template="Incomplete.",
            ),
            ScientificDecisionRule(
                rule_id="rule_beneficial",
                priority=20,
                description="All seeds improved",
                condition=AllSeedsImprovedCondition(),
                conclusion_code=ScientificConclusion.BENEFICIAL,
                narrative_template="Beneficial.",
            ),
            ScientificDecisionRule(
                rule_id="rule_worse",
                priority=30,
                description="All seeds degraded",
                condition=AllSeedsDegradedCondition(),
                conclusion_code=ScientificConclusion.WORSE,
                narrative_template="Worse.",
            ),
            ScientificDecisionRule(
                rule_id="rule_equivalent",
                priority=40,
                description="Within practical equivalence margin",
                condition=WithinEquivalenceMarginCondition(margin=0.01),
                conclusion_code=ScientificConclusion.PRACTICALLY_EQUIVALENT,
                narrative_template="Equivalent.",
            ),
            ScientificDecisionRule(
                rule_id="rule_mixed",
                priority=50,
                description="Mean improvement without unanimous seeds",
                condition=MeanImprovedAboveThresholdCondition(threshold=0.01),
                conclusion_code=ScientificConclusion.MIXED,
                narrative_template="Mixed.",
            ),
            ScientificDecisionRule(
                rule_id="rule_always",
                priority=99,
                description="Catch-all",
                condition=AlwaysCondition(),
                conclusion_code=ScientificConclusion.MIXED,
                narrative_template="Mixed.",
            ),
        ],
        plan_fingerprint="sp_fp_validator",
    )


def _trial_specs() -> ExperimentTrialSpecs:
    fit = TrialIntent(
        intent_id="fit_var_A",
        intent_type="variant_fit",
        description="Fit var_A",
        required_inputs=[],
        expected_outputs=[
            ArtifactRequirement(
                requirement_id="best_model_var_A",
                artifact_type="model_weights",
                description="Fitted weights",
            )
        ],
    )
    smoke = TrialIntent(
        intent_id="smoke_var_A",
        intent_type="smoke_inference",
        description="Smoke var_A",
        required_inputs=[
            ArtifactRequirement(
                requirement_id="model_weights_req_var_A",
                artifact_type="model_weights",
                description="Weights from fit",
            )
        ],
        expected_outputs=[
            ArtifactRequirement(
                requirement_id="smoke_metrics_var_A",
                artifact_type="metrics_json",
                description="Smoke metrics",
            )
        ],
    )
    full = TrialIntent(
        intent_id="full_var_A",
        intent_type="full_evaluation",
        description="Full var_A",
        required_inputs=[
            ArtifactRequirement(
                requirement_id="model_weights_req_var_A",
                artifact_type="model_weights",
                description="Weights from fit",
            )
        ],
        expected_outputs=[
            ArtifactRequirement(
                requirement_id="metrics_var_A",
                artifact_type="metrics_json",
                description="Full metrics",
            )
        ],
    )
    return ExperimentTrialSpecs(
        specs_id="specs_validator",
        schema_version=1,
        protocol_fingerprint="fp_validator",
        baseline=TrialIntent(
            intent_id="baseline_eval",
            intent_type="baseline_run",
            description="Run baseline",
            required_inputs=[],
            expected_outputs=[
                ArtifactRequirement(
                    requirement_id="baseline_metrics",
                    artifact_type="metrics_json",
                    description="Baseline metrics",
                )
            ],
        ),
        variants=[
            VariantTrialSpec(
                variant_id="var_A",
                variant_label="Variant A",
                idea_id="idea_A",
                primary_hook_id="hook_A",
                hook_bindings=[],
                interface_deltas=[],
                regime_changes=[],
                state_changes=[],
                adapter_required=False,
                new_dependencies=[],
                risk_level="low",
                preparation_phase=PreparationPhase.FIT,
                fit=fit,
                fit_seed_policy="shared_fixed",
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
                    "source_evidence_ids": ["ev_hp"],
                },
                evidence_ids=["ev_var_A"],
            )
        ],
    )


def _matrix() -> ExperimentMatrix:
    return ExperimentMatrix(
        matrix_id="matrix_validator",
        schema_version=1,
        protocol_fingerprint="fp_validator",
        seeds=[42],
        variants=["var_A"],
        entries=[
            MatrixEntry(
                entry_id="baseline_s42",
                variant_id=None,
                stage="baseline",
                seed=42,
                intent_ref="baseline_eval",
                depends_on=[],
                expected_outputs=[
                    ArtifactProduction(
                        production_id="baseline_metrics",
                        artifact_type="metrics_json",
                        description="Baseline metrics",
                    )
                ],
                shared_axes=["protocol"],
                independent_axes=["seed_42"],
            ),
            MatrixEntry(
                entry_id="var_A_fit_s42",
                variant_id="var_A",
                stage="fit",
                seed=42,
                intent_ref="fit_var_A",
                depends_on=[],
                expected_outputs=[
                    ArtifactProduction(
                        production_id="best_model_var_A",
                        artifact_type="model_weights",
                        description="Fitted weights",
                    )
                ],
                shared_axes=["protocol"],
                independent_axes=["fit_stage"],
            ),
            MatrixEntry(
                entry_id="var_A_smoke",
                variant_id="var_A",
                stage="smoke",
                seed=42,
                intent_ref="smoke_var_A",
                depends_on=["var_A_fit_s42"],
                expected_outputs=[
                    ArtifactProduction(
                        production_id="smoke_metrics_var_A",
                        artifact_type="metrics_json",
                        description="Smoke metrics",
                    )
                ],
                shared_axes=["protocol"],
                independent_axes=["smoke_check"],
            ),
            MatrixEntry(
                entry_id="var_A_full_s42",
                variant_id="var_A",
                stage="full",
                seed=42,
                intent_ref="full_var_A",
                depends_on=["var_A_smoke", "var_A_fit_s42"],
                expected_outputs=[
                    ArtifactProduction(
                        production_id="metrics_var_A",
                        artifact_type="metrics_json",
                        description="Full metrics",
                    )
                ],
                shared_axes=["protocol"],
                independent_axes=["seed_42"],
            ),
        ],
        input_bindings=[
            MatrixInputBinding(
                consumer_entry_id="var_A_smoke",
                consumer_requirement_id="model_weights_req_var_A",
                producer_entry_id="var_A_fit_s42",
                producer_production_id="best_model_var_A",
            ),
            MatrixInputBinding(
                consumer_entry_id="var_A_full_s42",
                consumer_requirement_id="model_weights_req_var_A",
                producer_entry_id="var_A_fit_s42",
                producer_production_id="best_model_var_A",
            ),
        ],
    )


def _resolution_plans() -> ExperimentalResolutionPlans:
    return ExperimentalResolutionPlans(
        plans_id="rp_validator",
        schema_version=1,
        protocol_fingerprint="fp_validator",
        resolutions=[
            ExperimentalResolutionPlan(
                unresolved_dimension_id="a" * 64,
                dimension="metrics_compatibility",
                variant_id="var_A",
                verification_stage="full",
                target_entry_ids=["var_A_full_s42"],
                observable="image_auroc",
                observation_source="metrics.json",
                acceptance_criterion=RangeCriterion(
                    metric_name="image_auroc",
                    lower_bound=0.0,
                    upper_bound=1.0,
                ),
                result_on_accept=ResolutionOutcome.RESOLVED_COMPATIBLE,
            )
        ],
    )


def _budget() -> ResourceBudget:
    limits = ResourceLimits(
        max_total_gpu_hours=10.0,
        max_per_experiment_gpu_hours=2.0,
        available_gpu_count=1,
        available_gpu_type="A100",
    )
    baseline_entry = _estimate("baseline_s42")
    variant_entries = [
        _estimate("var_A_fit_s42"),
        _estimate("var_A_smoke"),
        _estimate("var_A_full_s42"),
    ]
    total = ExperimentBundleResourceBudget(
        total_gpu_hours=4.0,
        total_wall_clock_hours=4.0,
        max_single_experiment_gpu_hours=1.0,
    )
    return ResourceBudget(
        budget_id="budget_validator",
        schema_version=1,
        protocol_fingerprint="fp_validator",
        protocol_version=1,
        limits=limits,
        per_variant={
            "baseline": {
                "variant_id": None,
                "entries": [baseline_entry],
                "total_gpu_hours": 1.0,
                "total_wall_clock_hours": 1.0,
            },
            "var_A": {
                "variant_id": "var_A",
                "entries": variant_entries,
                "total_gpu_hours": 3.0,
                "total_wall_clock_hours": 3.0,
            },
        },
        total_estimate=total,
        budget_decision=BudgetDecision(
            status="within_budget",
            original_limits=limits,
            estimated_consumption=total,
            utilization_pct=40.0,
        ),
    )


def _estimate(entry_id: str) -> EntryResourceEstimate:
    return EntryResourceEstimate(
        entry_id=entry_id,
        estimated_gpu_hours_low=0.5,
        estimated_gpu_hours_high=1.0,
        planning_value=1.0,
        safety_factor=1.0,
        estimate_source="fixture",
        confidence="high",
    )


def _guard_policy() -> OperationalGuardPolicy:
    return OperationalGuardPolicy(
        policy_id="guard_validator",
        schema_version=1,
        protocol_fingerprint="fp_validator",
        guards=[
            OperationalGuard(
                guard_id="g_timeout",
                guard_type="timeout",
                target_entry_ids=["*"],
                parameters={"max_seconds": 3600},
                action="stop_entry",
                is_blocking=False,
            )
        ],
    )
