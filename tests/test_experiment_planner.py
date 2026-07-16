"""Integration coverage for the retained, source-neutral experiment planner."""

from autoad_researcher.experiment.planner import (
    ExperimentPlanner,
    ExperimentPlannerRequest,
    StageResourceEstimateInput,
    StageResourceEstimateProfile,
)
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.experiment_planning import (
    AllSeedsDegradedCondition,
    AllSeedsImprovedCondition,
    AlwaysCondition,
    ExperimentPlanningInput,
    ExperimentVariantInput,
    IncompletePairsCondition,
    MeanImprovedAboveThresholdCondition,
    PlanningInputRefs,
    ResourceLimits,
    ScientificConclusion,
    ScientificDecisionRule,
    SupplementalEvaluationRefs,
    WithinEquivalenceMarginCondition,
)


def test_experiment_planner_emits_validated_bundle_without_a_transfer_handoff(tmp_path):
    estimate = StageResourceEstimateInput(estimated_gpu_hours_low=.5, estimated_gpu_hours_high=1, planning_value=1, estimate_source="test", confidence="high")
    result = ExperimentPlanner(runs_root=tmp_path).run(ExperimentPlannerRequest(
        planning_input=ExperimentPlanningInput(run_id="run_35", variants=[ExperimentVariantInput(variant_id="var_A", variant_label="Variant A", idea_id="idea_001", primary_hook_id="hook_01", risk_level="low", evidence_ids=["ev_hp"])]),
        source_input_sha256="f" * 64,
        planning_input_refs=PlanningInputRefs(repository_fingerprint="repo_fp", environment_sha256="a" * 64, dataset_manifest_sha256="b" * 64, asset_manifest_sha256="c" * 64),
        supplemental_refs=SupplementalEvaluationRefs(evaluator_coverage_evidence_ids=["ev_eval"], metric_parser_coverage_evidence_ids=["ev_parser"], postprocessing_coverage_evidence_ids=["ev_post"], dataset_split_coverage_evidence_ids=["ev_split"]),
        evaluation_protocol_ref=ArtifactReferenceV2(artifact_id="eval", artifact_type="config", locator="runs/run_35/eval.json", sha256="d" * 64),
        baseline_method="baseline", baseline_config_sha256="e" * 64, seeds=[42], primary_metric="auroc", metric_direction="maximize", protocol_evidence_ids=["ev_protocol"],
        decision_rules=_rules(), resource_limits=ResourceLimits(max_total_gpu_hours=10, max_per_experiment_gpu_hours=2, available_gpu_count=1, available_gpu_type="A100"),
        resource_estimates=StageResourceEstimateProfile(baseline=estimate, fit=estimate, smoke=estimate, full=estimate),
    ))

    assert result.validation_report.status == "passed"
    assert result.handoff.next_stage == "awaiting_implementation_approval"
    assert result.handoff.selected_variant_ids == ["var_A"]


def _rules() -> list[ScientificDecisionRule]:
    return [
        ScientificDecisionRule(rule_id="incomplete", priority=10, description="Incomplete", condition=IncompletePairsCondition(min_pairs=1), conclusion_code=ScientificConclusion.INCOMPLETE, narrative_template="Incomplete."),
        ScientificDecisionRule(rule_id="better", priority=20, description="Better", condition=AllSeedsImprovedCondition(), conclusion_code=ScientificConclusion.BENEFICIAL, narrative_template="Better."),
        ScientificDecisionRule(rule_id="worse", priority=30, description="Worse", condition=AllSeedsDegradedCondition(), conclusion_code=ScientificConclusion.WORSE, narrative_template="Worse."),
        ScientificDecisionRule(rule_id="equal", priority=40, description="Equal", condition=WithinEquivalenceMarginCondition(margin=.01), conclusion_code=ScientificConclusion.PRACTICALLY_EQUIVALENT, narrative_template="Equal."),
        ScientificDecisionRule(rule_id="mixed", priority=50, description="Mixed", condition=MeanImprovedAboveThresholdCondition(threshold=.01), conclusion_code=ScientificConclusion.MIXED, narrative_template="Mixed."),
        ScientificDecisionRule(rule_id="default", priority=99, description="Default", condition=AlwaysCondition(), conclusion_code=ScientificConclusion.MIXED, narrative_template="Default."),
    ]
