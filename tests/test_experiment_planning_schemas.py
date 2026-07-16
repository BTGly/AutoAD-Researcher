"""Schema contract tests for experiment_planning.py (Step 3.5)."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from autoad_researcher.schemas.experiment_planning import (
    PLANNING_ARTIFACT_PATHS,
    AlwaysCondition,
    ArtifactManifest,
    ArtifactProduction,
    ArtifactRequirement,
    BaselineExecutionPolicy,
    BaselineResultRef,
    BudgetDecision,
    DatasetPartitionRef,
    EntryResourceEstimate,
    ExperimentBundleResourceBudget,
    ExperimentMatrix,
    ExperimentPlanValidationIssue,
    ExperimentPlanValidationReport,
    ExperimentPlannerHandoff,
    ExperimentTrialSpecs,
    ExperimentalResolutionPlan,
    ExperimentalResolutionPlans,
    HyperparameterPlan,
    IncompletePairsCondition,
    ManifestEntry,
    MatrixEntry,
    MixedDirectionCondition,
    OperationalGuard,
    OperationalGuardPolicy,
    PlanningInputRefs,
    PreparationPhase,
    RangeCriterion,
    ResolutionOutcome,
    ResourceBudget,
    ResourceLimits,
    ScientificConclusion,
    ScientificDecisionRule,
    SearchBudget,
    SeedMetric,
    SharedExperimentProtocol,
    StatisticalAnalysisPlan,
    SupplementalEvaluationRefs,
    TrialIntent,
    ValidatedArtifactRef,
    VariantTrialSpec,
    WithinEquivalenceMarginCondition,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_artifact_refv2(artifact_id="art_01", artifact_type="metrics_json", sha256="a" * 64):
    from autoad_researcher.schemas.artifacts import ArtifactReferenceV2

    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        locator=f"runs/run1/{artifact_id}.json",
        sha256=sha256,
    )


def _mock_validated_ref(path="shared_experiment_protocol.json", sha256="a" * 64):
    return ValidatedArtifactRef(relative_path=path, sha256=sha256)


def _validated_refs_all():
    return [_mock_validated_ref(p) for p in PLANNING_ARTIFACT_PATHS]


def _mock_seed_metrics():
    return [SeedMetric(seed=42, metric_name="image_auroc", metric_value=0.85)]


def _mock_baseline_result_ref():
    return BaselineResultRef(
        source_run_id="run_baseline_001",
        repository_fingerprint="abc123",
        baseline_config_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        evaluation_contract_sha256="c" * 64,
        environment_lock_sha256="d" * 64,
        asset_manifest_sha256="e" * 64,
        command_sha256="f" * 64,
        seeds=[42],
        per_seed_metrics=_mock_seed_metrics(),
        result_artifact_refs=[_mock_artifact_refv2()],
        validity_report_ref=_mock_artifact_refv2("vrpt_01", "report"),
        validity_status="valid",
        completed_seed_ids=[42],
    )


def _mock_planning_input_refs():
    return PlanningInputRefs(
        repository_fingerprint="repo_fp",
        environment_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        asset_manifest_sha256="c" * 64,
    )


def _mock_supplemental_refs():
    return SupplementalEvaluationRefs(
        evaluator_ref=_mock_artifact_refv2("eval_01"),
        metric_parser_ref=None,
        postprocessing_ref=None,
        dataset_split_ref=None,
    )


def _mock_baseline_policy(mode="run_fresh"):
    return BaselineExecutionPolicy(
        mode=mode,
        reuse_source=None if mode == "run_fresh" else _mock_baseline_result_ref(),
        seeds=[42],
    )


def _mock_protocol():
    return SharedExperimentProtocol(
        protocol_id="proto_001",
        schema_version=1,
        planning_input_refs=_mock_planning_input_refs(),
        supplemental_refs=_mock_supplemental_refs(),
        evaluation_protocol_ref=_mock_artifact_refv2("eval_proto"),
        baseline_method="patchcore",
        baseline_config_sha256="g" * 64,
        baseline_policy=_mock_baseline_policy(),
        seeds=[42],
        primary_metric="image_auroc",
        metric_direction="maximize",
        protected_paths=["src/models/patchcore.py"],
        must_not_change=[],
        protocol_evidence_ids=["ev_01"],
        protocol_fingerprint="fp_abc",
    )


def _mock_stat_plan():
    return StatisticalAnalysisPlan(
        plan_id="sp_001",
        schema_version=1,
        protocol_fingerprint="fp_abc",
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
                rule_id="rule_always",
                priority=99,
                description="Catch-all",
                condition=AlwaysCondition(),
                conclusion_code=ScientificConclusion.MIXED,
                narrative_template="Mixed results",
            ),
        ],
        plan_fingerprint="sp_fp",
    )


def _mock_trial_intent(intent_id="intent_01", intent_type="full_evaluation"):
    return TrialIntent(
        intent_id=intent_id,
        intent_type=intent_type,
        description="Run full evaluation",
        required_inputs=[
            ArtifactRequirement(
                requirement_id="weights_req",
                artifact_type="model_weights",
                description="Model weights",
            ),
        ],
        expected_outputs=[
            ArtifactRequirement(
                requirement_id="metrics_out",
                artifact_type="metrics_json",
                description="Evaluation metrics",
            ),
        ],
    )


def _mock_variant_trial_spec(variant_id="var_A"):
    return VariantTrialSpec(
        variant_id=variant_id,
        variant_label="Variant A",
        idea_id="idea_001",
        primary_hook_id="hook_01",
        hook_bindings=[],
        interface_deltas=[],
        regime_changes=[],
        state_changes=[],
        adapter_required=False,
        new_dependencies=[],
        risk_level="low",
        preparation_phase=PreparationPhase.NONE,
        fit=None,
        fit_seed_policy=None,
        smoke=_mock_trial_intent("smoke_A", "smoke_inference"),
        full=_mock_trial_intent("full_A", "full_evaluation"),
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
        hyperparameter_plan=_mock_hp_plan(),
        evidence_ids=["ev_A"],
    )


def _mock_hp_plan():
    return HyperparameterPlan(
        mode="fixed_from_source",
        source_evidence_ids=["ev_hp"],
    )


def _mock_trial_specs():
    return ExperimentTrialSpecs(
        specs_id="specs_001",
        schema_version=1,
        protocol_fingerprint="fp_abc",
        baseline=_mock_trial_intent("baseline_intent", "baseline_run"),
        variants=[_mock_variant_trial_spec("var_A")],
    )


def _mock_matrix():
    return ExperimentMatrix(
        matrix_id="matrix_001",
        schema_version=1,
        protocol_fingerprint="fp_abc",
        seeds=[42],
        variants=["var_A"],
        entries=[
            MatrixEntry(
                entry_id="baseline_s42",
                variant_id=None,
                stage="baseline",
                seed=42,
                intent_ref="baseline_intent",
                depends_on=[],
                shared_axes=["protocol"],
                independent_axes=["seed_42"],
            ),
            MatrixEntry(
                entry_id="var_A_smoke",
                variant_id="var_A",
                stage="smoke",
                seed=42,
                intent_ref="smoke_A",
                depends_on=[],
                shared_axes=["protocol"],
                independent_axes=["smoke_check"],
            ),
            MatrixEntry(
                entry_id="var_A_full_s42",
                variant_id="var_A",
                stage="full",
                seed=42,
                intent_ref="full_A",
                depends_on=["var_A_smoke"],
                shared_axes=["protocol"],
                independent_axes=["seed_42"],
            ),
        ],
    )


def _mock_budget():
    limits = ResourceLimits(
        max_total_gpu_hours=10.0,
        max_per_experiment_gpu_hours=2.0,
        available_gpu_count=1,
        available_gpu_type="A100",
    )
    est = ExperimentBundleResourceBudget(
        total_gpu_hours=5.0,
        total_wall_clock_hours=2.0,
        max_single_experiment_gpu_hours=1.0,
    )
    decision = BudgetDecision(
        status="within_budget",
        original_limits=limits,
        estimated_consumption=est,
        utilization_pct=50.0,
    )
    return ResourceBudget(
        budget_id="budget_001",
        schema_version=1,
        protocol_fingerprint="fp_abc",
        protocol_version=1,
        limits=limits,
        per_variant={},
        total_estimate=est,
        budget_decision=decision,
    )


# ---------------------------------------------------------------------------
# Schemas — extra="forbid"
# ---------------------------------------------------------------------------

FORBIDDEN_SCHEMAS = [
    PlanningInputRefs,
    SharedExperimentProtocol,
    StatisticalAnalysisPlan,
    ExperimentTrialSpecs,
    ExperimentMatrix,
    ExperimentalResolutionPlans,
    EntryResourceEstimate,
    ResourceBudget,
    OperationalGuardPolicy,
    ExperimentPlanValidationReport,
    ExperimentPlannerHandoff,
]


@pytest.mark.parametrize("schema_cls", FORBIDDEN_SCHEMAS)
def test_schema_extra_forbid(schema_cls):
    assert schema_cls.model_config.get("extra") == "forbid", (
        f"{schema_cls.__name__} must have extra='forbid'"
    )


# ---------------------------------------------------------------------------
# PlanningInputRefs
# ---------------------------------------------------------------------------

def test_planning_input_refs_no_command_sha256():
    p = PlanningInputRefs(
        repository_fingerprint="r",
        environment_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        asset_manifest_sha256="c" * 64,
    )
    d = p.model_dump()
    assert "command_sha256" not in d


# ---------------------------------------------------------------------------
# SharedExperimentProtocol — seeds + baseline_policy
# ---------------------------------------------------------------------------

def test_protocol_duplicate_seeds_rejected():
    with pytest.raises(ValidationError, match="unique"):
        SharedExperimentProtocol(
            protocol_id="p",
            schema_version=1,
            planning_input_refs=_mock_planning_input_refs(),
            supplemental_refs=_mock_supplemental_refs(),
            evaluation_protocol_ref=_mock_artifact_refv2(),
            baseline_method="bm",
            baseline_config_sha256="a" * 64,
            baseline_policy=BaselineExecutionPolicy(mode="run_fresh", seeds=[42, 42]),
            seeds=[42, 42],
            primary_metric="auroc",
            metric_direction="maximize",
            protected_paths=[],
            must_not_change=[],
            protocol_evidence_ids=[],
            protocol_fingerprint="fp",
        )


def test_protocol_baseline_policy_seeds_mismatch():
    with pytest.raises(ValidationError, match="baseline_policy"):
        SharedExperimentProtocol(
            protocol_id="p",
            schema_version=1,
            planning_input_refs=_mock_planning_input_refs(),
            supplemental_refs=_mock_supplemental_refs(),
            evaluation_protocol_ref=_mock_artifact_refv2(),
            baseline_method="bm",
            baseline_config_sha256="a" * 64,
            baseline_policy=BaselineExecutionPolicy(mode="run_fresh", seeds=[43]),
            seeds=[42],
            primary_metric="auroc",
            metric_direction="maximize",
            protected_paths=[],
            must_not_change=[],
            protocol_evidence_ids=[],
            protocol_fingerprint="fp",
        )


def test_protocol_reuse_missing_source():
    with pytest.raises(ValidationError, match="reuse_existing"):
        BaselineExecutionPolicy(mode="reuse_existing", reuse_source=None, seeds=[42])


def test_protocol_run_fresh_has_source():
    src = _mock_baseline_result_ref()
    with pytest.raises(ValidationError, match="run_fresh"):
        BaselineExecutionPolicy(mode="run_fresh", reuse_source=src, seeds=[42])


# ---------------------------------------------------------------------------
# StatisticalAnalysisPlan — discriminated union + rule coverage
# ---------------------------------------------------------------------------

def test_condition_discriminator_resolves():
    """DecisionConditionUnion must resolve AlwaysCondition via discriminator."""
    sp = StatisticalAnalysisPlan(
        plan_id="sp_001",
        schema_version=1,
        protocol_fingerprint="fp_abc",
        primary_metric="auroc",
        metric_direction="maximize",
        aggregation="mean",
        dispersion="std",
        missing_run_policy="report_incomplete",
        multiple_variant_policy="descriptive_only",
        decision_rules=[
            ScientificDecisionRule(
                rule_id="r1",
                priority=99,
                description="catch-all",
                condition=AlwaysCondition(),
                conclusion_code=ScientificConclusion.MIXED,
                narrative_template="Mixed.",
            ),
        ],
        plan_fingerprint="sp_fp",
    )
    rule = sp.decision_rules[0]
    assert isinstance(rule.condition, AlwaysCondition)
    assert rule.condition.condition_type == "always"


def test_condition_discriminator_rejects_unknown():
    """Extra condition_type should be rejected by discriminator."""
    with pytest.raises(ValidationError):
        ScientificDecisionRule(
            rule_id="r2",
            priority=10,
            description="bad",
            condition={"condition_type": "nonexistent_x"},
            conclusion_code=ScientificConclusion.BENEFICIAL,
            narrative_template="No.",
        )


def test_all_seeds_improved_condition_valid():
    from autoad_researcher.schemas.experiment_planning import AllSeedsImprovedCondition

    c = AllSeedsImprovedCondition()
    assert c.condition_type == "all_seeds_improved"


def test_incomplete_pairs_condition_min_pairs():
    c = IncompletePairsCondition(min_pairs=5)
    assert c.min_pairs == 5


def test_mean_improved_threshold_ge_zero():
    from autoad_researcher.schemas.experiment_planning import MeanImprovedAboveThresholdCondition

    with pytest.raises(ValidationError):
        MeanImprovedAboveThresholdCondition(threshold=-0.1)


def test_within_equivalence_margin_ge_zero():
    with pytest.raises(ValidationError):
        WithinEquivalenceMarginCondition(margin=-0.1)


# ---------------------------------------------------------------------------
# VariantTrialSpec — preparation_phase ↔ fit
# ---------------------------------------------------------------------------

def test_variant_no_fit_no_policy():
    v = _mock_variant_trial_spec("var_B")
    v.preparation_phase = PreparationPhase.NONE
    v.fit = None
    v.fit_seed_policy = None
    # Should be fine


def test_variant_fit_without_preparation_rejected():
    v = _mock_variant_trial_spec("var_C")
    v.preparation_phase = PreparationPhase.NONE
    v.fit = _mock_trial_intent("fit_C", "variant_fit")
    v.fit_seed_policy = "shared_fixed"
    with pytest.raises(ValidationError, match="fit intent present but preparation_phase"):
        VariantTrialSpec(**v.model_dump())


def test_variant_preparation_fit_without_intent_rejected():
    v = _mock_variant_trial_spec("var_D")
    v.preparation_phase = PreparationPhase.FIT
    v.fit = None
    v.fit_seed_policy = "shared_fixed"
    with pytest.raises(ValidationError, match="requires fit but fit intent is None"):
        VariantTrialSpec(**v.model_dump())


def test_variant_preparation_fit_without_policy_rejected():
    v = _mock_variant_trial_spec("var_E")
    v.preparation_phase = PreparationPhase.FIT
    v.fit = _mock_trial_intent("fit_E", "variant_fit")
    v.fit_seed_policy = None
    with pytest.raises(ValidationError, match="fit intent present but fit_seed_policy"):
        VariantTrialSpec(**v.model_dump())


def test_variant_trait_without_fit_and_policy_rejected():
    v = _mock_variant_trial_spec("var_F")
    v.preparation_phase = PreparationPhase.NONE
    v.fit = None
    v.fit_seed_policy = "shared_fixed"
    with pytest.raises(ValidationError, match="fit_seed_policy set but no fit intent"):
        VariantTrialSpec(**v.model_dump())


def test_experiment_trial_specs_duplicate_variant_rejected():
    with pytest.raises(ValidationError, match="duplicate variant_id"):
        ExperimentTrialSpecs(
            specs_id="s",
            schema_version=1,
            protocol_fingerprint="fp",
            variants=[
                _mock_variant_trial_spec("var_A"),
                _mock_variant_trial_spec("var_A"),
            ],
        )


# ---------------------------------------------------------------------------
# HyperparameterPlan — test split leak
# ---------------------------------------------------------------------------

def test_dataset_partition_test_role():
    """DatasetPartitionRef.declared_role == 'test' should be rejected by validator downstream."""
    hp = HyperparameterPlan(
        mode="predeclared_search",
        source_evidence_ids=["ev"],
        search_space=[],
        selection_metric="auroc",
        selection_split=DatasetPartitionRef(
            partition_id="test_split",
            dataset_manifest_sha256="a" * 64,
            declared_role="test",
            evidence_ids=["ev"],
        ),
        search_budget=SearchBudget(max_trials=10, max_gpu_hours=5.0),
    )
    # Schema accepts it — validator must catch it separately
    assert hp.selection_split.declared_role == "test"


# ---------------------------------------------------------------------------
# ResolutionCriterion — discriminated union
# ---------------------------------------------------------------------------

def test_range_criterion_accepts_valid():
    c = RangeCriterion(metric_name="auroc", lower_bound=0.0, upper_bound=1.0)
    assert c.criterion_type == "value_in_range"


def test_range_criterion_rejects_inverted_bounds():
    with pytest.raises(ValidationError, match="lower_bound must be"):
        RangeCriterion(metric_name="auroc", lower_bound=1.0, upper_bound=0.0)


def test_experimental_resolution_plan_reject_pair_required():
    with pytest.raises(ValidationError, match="rejection_criterion and result_on_reject"):
        ExperimentalResolutionPlan(
            unresolved_dimension_id="a" * 64,
            dimension="training",
            variant_id="var_A",
            verification_stage="full",
            target_entry_ids=["e1"],
            observable="loss",
            observation_source="metrics.json",
            acceptance_criterion=RangeCriterion(
                metric_name="loss", lower_bound=0.0, upper_bound=1.0
            ),
            rejection_criterion=None,
            result_on_accept=ResolutionOutcome.RESOLVED_COMPATIBLE,
            result_on_reject=ResolutionOutcome.RESOLVED_INCOMPATIBLE,
        )


def test_accept_cannot_be_inconclusive():
    with pytest.raises(ValidationError, match="accept branch cannot"):
        ExperimentalResolutionPlan(
            unresolved_dimension_id="a" * 64,
            dimension="training",
            variant_id="var_A",
            verification_stage="full",
            target_entry_ids=["e1"],
            observable="loss",
            observation_source="metrics.json",
            acceptance_criterion=RangeCriterion(
                metric_name="loss", lower_bound=0.0, upper_bound=1.0
            ),
            result_on_accept=ResolutionOutcome.INCONCLUSIVE,
        )


# ---------------------------------------------------------------------------
# EntryResourceEstimate — intervals + low-confidence guard
# ---------------------------------------------------------------------------

def test_estimate_low_gt_high_rejected():
    with pytest.raises(ValidationError, match="low estimate must be"):
        EntryResourceEstimate(
            entry_id="e1",
            estimated_gpu_hours_low=5.0,
            estimated_gpu_hours_high=3.0,
            planning_value=5.0,
            estimate_source="manual",
            confidence="high",
        )


def test_estimate_low_confidence_planning_below_min():
    with pytest.raises(ValidationError, match="planning_value.*must be >="):
        EntryResourceEstimate(
            entry_id="e1",
            estimated_gpu_hours_low=3.0,
            estimated_gpu_hours_high=5.0,
            planning_value=4.0,
            safety_factor=2.0,
            estimate_source="manual",
            confidence="low",
        )


def test_estimate_high_confidence_accepts_low_planning():
    e = EntryResourceEstimate(
        entry_id="e1",
        estimated_gpu_hours_low=3.0,
        estimated_gpu_hours_high=5.0,
        planning_value=3.0,
        safety_factor=2.0,
        estimate_source="manual",
        confidence="high",
    )
    assert e.planning_value == 3.0


# ---------------------------------------------------------------------------
# ResourceLimits — numeric bounds
# ---------------------------------------------------------------------------

def test_resource_limits_negative_gpu_hours():
    with pytest.raises(ValidationError):
        ResourceLimits(
            max_total_gpu_hours=-1.0,
            max_per_experiment_gpu_hours=1.0,
            available_gpu_count=1,
            available_gpu_type="A100",
        )


def test_resource_limits_zero_gpu_count():
    with pytest.raises(ValidationError):
        ResourceLimits(
            max_total_gpu_hours=10.0,
            max_per_experiment_gpu_hours=1.0,
            available_gpu_count=0,
            available_gpu_type="A100",
        )


# ---------------------------------------------------------------------------
# BudgetDecision — state machine
# ---------------------------------------------------------------------------

def test_budget_decision_within_budget_with_items_rejected():
    limits = ResourceLimits(max_total_gpu_hours=10, max_per_experiment_gpu_hours=2, available_gpu_count=1, available_gpu_type="A100")
    est = ExperimentBundleResourceBudget(total_gpu_hours=5, total_wall_clock_hours=2, max_single_experiment_gpu_hours=1)
    with pytest.raises(ValidationError, match="within_budget cannot contain"):
        BudgetDecision(
            status="within_budget",
            original_limits=limits,
            estimated_consumption=est,
            utilization_pct=50.0,
            over_budget_items=["GPU hours exceeded"],
        )


def test_budget_decision_revision_selected_without_option_id():
    limits = ResourceLimits(max_total_gpu_hours=10, max_per_experiment_gpu_hours=2, available_gpu_count=1, available_gpu_type="A100")
    est = ExperimentBundleResourceBudget(total_gpu_hours=15, total_wall_clock_hours=5, max_single_experiment_gpu_hours=3)
    with pytest.raises(ValidationError, match="revision_selected requires"):
        BudgetDecision(
            status="revision_selected",
            original_limits=limits,
            estimated_consumption=est,
            utilization_pct=150.0,
            over_budget_items=["total_gpu_hours"],
            selected_revision_option_id=None,
        )


def test_budget_decision_override_confirmed_without_approved_limits():
    limits = ResourceLimits(max_total_gpu_hours=10, max_per_experiment_gpu_hours=2, available_gpu_count=1, available_gpu_type="A100")
    est = ExperimentBundleResourceBudget(total_gpu_hours=15, total_wall_clock_hours=5, max_single_experiment_gpu_hours=3)
    with pytest.raises(ValidationError, match="override_confirmed requires approved_limits"):
        BudgetDecision(
            status="override_confirmed",
            original_limits=limits,
            estimated_consumption=est,
            utilization_pct=150.0,
            over_budget_items=["total_gpu_hours"],
            user_decision_evidence_id="ev_01",
            decided_at=datetime.now(timezone.utc),
            approved_limits=None,
        )


def test_budget_decision_override_rejected_needs_evidence():
    limits = ResourceLimits(max_total_gpu_hours=10, max_per_experiment_gpu_hours=2, available_gpu_count=1, available_gpu_type="A100")
    est = ExperimentBundleResourceBudget(total_gpu_hours=15, total_wall_clock_hours=5, max_single_experiment_gpu_hours=3)
    with pytest.raises(ValidationError, match="requires user_decision_evidence_id"):
        BudgetDecision(
            status="override_rejected",
            original_limits=limits,
            estimated_consumption=est,
            utilization_pct=150.0,
            over_budget_items=["total_gpu_hours"],
            user_decision_evidence_id=None,
        )


# ---------------------------------------------------------------------------
# ValidatedArtifactRef + ValidationReport
# ---------------------------------------------------------------------------

def test_validated_artifact_ref_valid():
    ref = ValidatedArtifactRef(relative_path="shared_experiment_protocol.json", sha256="a" * 64)
    assert ref.relative_path == "shared_experiment_protocol.json"


def test_validated_artifact_ref_invalid_path():
    with pytest.raises(ValidationError):
        ValidatedArtifactRef(relative_path="not_a_valid_path.json", sha256="a" * 64)


def test_validated_artifact_ref_invalid_sha():
    with pytest.raises(ValidationError):
        ValidatedArtifactRef(relative_path="shared_experiment_protocol.json", sha256="short")


def test_validation_report_exactly_7_refs():
    """Report must have exactly 7 validated_artifact_refs covering all planning artifacts."""
    refs = _validated_refs_all()
    report = ExperimentPlanValidationReport(
        report_id="rpt_001",
        run_id="run_001",
        protocol_fingerprint="fp_abc",
        status="passed",
        validated_artifact_refs=refs,
    )
    assert len(report.validated_artifact_refs) == 7


def test_validation_report_missing_artifact_rejected():
    refs = [_mock_validated_ref(p) for p in PLANNING_ARTIFACT_PATHS[:6]]
    with pytest.raises(ValidationError, match="passed report must bind all 7"):
        ExperimentPlanValidationReport(
            report_id="rpt_001",
            run_id="run_001",
            protocol_fingerprint="fp_abc",
            status="passed",
            validated_artifact_refs=refs,
        )


def test_failed_validation_report_allows_artifact_ref_subset():
    issue = ExperimentPlanValidationIssue(
        issue_id="i_01",
        severity="blocking",
        invariant_category="structure",
        message="Missing file",
    )
    refs = [_mock_validated_ref(p) for p in PLANNING_ARTIFACT_PATHS[:6]]
    report = ExperimentPlanValidationReport(
        report_id="rpt_001",
        run_id="run_001",
        protocol_fingerprint="fp_abc",
        status="failed",
        issues=[issue],
        validated_artifact_refs=refs,
    )
    assert len(report.validated_artifact_refs) == 6


def test_validation_report_duplicate_path_rejected():
    refs = _validated_refs_all()
    refs.append(_mock_validated_ref("shared_experiment_protocol.json", "b" * 64))
    with pytest.raises(ValidationError):
        ExperimentPlanValidationReport(
            report_id="rpt_001",
            run_id="run_001",
            protocol_fingerprint="fp_abc",
            status="passed",
            validated_artifact_refs=refs,
        )


def test_validation_report_status_must_match_issues():
    issue = ExperimentPlanValidationIssue(
        issue_id="i_01",
        severity="blocking",
        invariant_category="structure",
        message="Missing file",
    )
    with pytest.raises(ValidationError, match="inconsistent"):
        ExperimentPlanValidationReport(
            report_id="rpt_001",
            run_id="run_001",
            protocol_fingerprint="fp_abc",
            status="passed",
            issues=[issue],
            validated_artifact_refs=_validated_refs_all(),
        )


# ---------------------------------------------------------------------------
# ManifestEntry — path safety
# ---------------------------------------------------------------------------

def test_manifest_entry_parent_traversal_rejected():
    with pytest.raises(ValidationError, match="parent traversal"):
        ManifestEntry(relative_path="../secret.json", sha256="a" * 64, artifact_type="x")


def test_manifest_entry_absolute_path_rejected():
    with pytest.raises(ValidationError, match="absolute path"):
        ManifestEntry(relative_path="/etc/passwd", sha256="a" * 64, artifact_type="x")


def test_manifest_entry_valid_relative():
    m = ManifestEntry(
        relative_path="shared_experiment_protocol.json",
        sha256="a" * 64,
        artifact_type="protocol",
    )
    assert m.relative_path == "shared_experiment_protocol.json"


# ---------------------------------------------------------------------------
# ExperimentPlannerHandoff
# ---------------------------------------------------------------------------

def test_handoff_construction():
    h = ExperimentPlannerHandoff(
        schema_version=1,
        run_id="run_001",
        source_input_sha256="a" * 64,
        artifact_manifest=ArtifactManifest(
            entries=[
                ManifestEntry(
                    relative_path="shared_experiment_protocol.json",
                    sha256="a" * 64,
                    artifact_type="protocol",
                ),
            ]
        ),
        selected_variant_ids=["var_A"],
        validation_report_sha256="b" * 64,
        next_stage="awaiting_implementation_approval",
    )
    assert h.next_stage == "awaiting_implementation_approval"


# ---------------------------------------------------------------------------
# OperationalGuardPolicy
# ---------------------------------------------------------------------------

def test_operational_guard_valid():
    g = OperationalGuard(
        guard_id="g1",
        guard_type="timeout",
        target_entry_ids=["*"],
        parameters={"max_seconds": 3600},
        action="stop_entry",
        is_blocking=False,
    )
    p = OperationalGuardPolicy(
        policy_id="gp_001",
        schema_version=1,
        protocol_fingerprint="fp",
        guards=[g],
    )
    assert len(p.guards) == 1


# ---------------------------------------------------------------------------
# TrialIntent — no depends_on, no timeout_policy_ref, no shell
# ---------------------------------------------------------------------------

def test_trial_intent_no_depends_on():
    t = _mock_trial_intent()
    d = t.model_dump()
    assert "depends_on" not in d
    assert "timeout_policy_ref" not in d
    assert "shell" not in d


# ---------------------------------------------------------------------------
# MatrixEntry — no resource fields
# ---------------------------------------------------------------------------

def test_matrix_entry_no_resource_fields():
    m = MatrixEntry(
        entry_id="e1",
        variant_id=None,
        stage="baseline",
        seed=42,
        intent_ref="intent_01",
        depends_on=[],
        shared_axes=["protocol"],
        independent_axes=["seed_42"],
    )
    d = m.model_dump()
    assert "estimated_gpu_hours" not in d


# ---------------------------------------------------------------------------
# ExperimentTrialSpecs — baseline can be None (reuse)
# ---------------------------------------------------------------------------

def test_trial_specs_baseline_none():
    specs = ExperimentTrialSpecs(
        specs_id="s",
        schema_version=1,
        protocol_fingerprint="fp",
        baseline=None,
        variants=[_mock_variant_trial_spec("var_A")],
    )
    assert specs.baseline is None


# ---------------------------------------------------------------------------
# ArtifactProduction from intent derivation pattern
# ---------------------------------------------------------------------------

def test_artifact_production_from_intent():
    intent = _mock_trial_intent("fit_intent", "variant_fit")
    productions = [
        ArtifactProduction(
            production_id=r.requirement_id,
            artifact_type="model_weights" if r.artifact_type == "model_weights" else r.artifact_type,
            description=r.description,
        )
        for r in intent.expected_outputs
    ]
    assert len(productions) == 1
    assert productions[0].production_id == "metrics_out"
