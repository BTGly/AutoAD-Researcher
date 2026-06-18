"""Integration tests for the Step 3.5 ExperimentPlanner orchestrator."""

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
    IncompletePairsCondition,
    MeanImprovedAboveThresholdCondition,
    PlanningInputRefs,
    ResourceLimits,
    ScientificConclusion,
    ScientificDecisionRule,
    SupplementalEvaluationRefs,
    WithinEquivalenceMarginCondition,
)
from autoad_researcher.schemas.transfer_design import (
    DerivedClaim,
    IdeaContract,
    IdeaTransferAnalysis,
    IdeaTransferDesignHandoff,
    ImplementationVariant,
    TransferStatus,
    UserProvidedIdeaContract,
    VariantRiskReport,
    VariantTransferAnalysis,
)


def test_experiment_planner_runs_3_4_handoff_to_3_6_handoff(tmp_path):
    request = ExperimentPlannerRequest(
        handoff=_handoff(),
        source_handoff_sha256="f" * 64,
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
            locator="runs/run_35/eval_proto.json",
            sha256="d" * 64,
        ),
        baseline_method="patchcore",
        baseline_config_sha256="e" * 64,
        seeds=[42],
        primary_metric="image_auroc",
        metric_direction="maximize",
        protocol_evidence_ids=["ev_protocol"],
        decision_rules=_decision_rules(),
        resource_limits=ResourceLimits(
            max_total_gpu_hours=10.0,
            max_per_experiment_gpu_hours=2.0,
            available_gpu_count=1,
            available_gpu_type="A100",
        ),
        resource_estimates=_resource_profile(),
    )

    result = ExperimentPlanner(runs_root=tmp_path).run(request)

    assert result.validation_report.status == "passed"
    assert result.handoff.next_stage == "3.6_patch_planner"
    assert result.handoff.selected_variant_ids == ["var_A"]
    assert len(result.artifact_paths) == 9
    for path in result.artifact_paths.values():
        assert (tmp_path / "run_35").resolve() in _parents(path)


def _handoff() -> IdeaTransferDesignHandoff:
    variant = ImplementationVariant(
        variant_id="var_A",
        variant_label="Variant A",
        idea_id="idea_001",
        primary_hook_id="hook_01",
        hook_bindings=[],
        interface_deltas=[],
        regime_changes=[],
        state_changes=[],
        adapter_required=False,
        new_dependencies=[],
        expected_behavior_rationale="Use the confirmed idea in the baseline",
        risk_level="low",
        fallback_behavior="Keep baseline behavior",
        idea_contract_evidence_ids=["ev_hp"],
    )
    return IdeaTransferDesignHandoff(
        run_id="run_35",
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
                "var_A": VariantTransferAnalysis(
                    variant_id="var_A",
                    overall_status=TransferStatus.VIABLE,
                )
            },
        ),
        transfer_constraints=[],
        selected_variants=[variant],
        variant_selection_sha256="c" * 64,
        variant_risk_reports=[
            VariantRiskReport(variant_id="var_A", computed_risk_level="low")
        ],
        validator_report_sha256="d" * 64,
    )


def _decision_rules() -> list[ScientificDecisionRule]:
    return [
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
            description="Within equivalence margin",
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
    ]


def _resource_profile() -> StageResourceEstimateProfile:
    estimate = StageResourceEstimateInput(
        estimated_gpu_hours_low=0.5,
        estimated_gpu_hours_high=1.0,
        planning_value=1.0,
        estimate_source="integration_test",
        confidence="high",
    )
    return StageResourceEstimateProfile(
        baseline=estimate,
        fit=estimate,
        smoke=estimate,
        full=estimate,
    )


def _parents(path: str):
    from pathlib import Path

    return set(Path(path).resolve().parents)
