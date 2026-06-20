"""Stage 3.5 experiment_planner runner — wraps ExperimentPlanner into pipeline."""

import json
from pathlib import Path

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
from autoad_researcher.schemas.stage3_acceptance import (
    Stage3AcceptanceArtifactRef,
    Stage3AcceptanceStageRecord,
)
from autoad_researcher.schemas.transfer_design import IdeaTransferDesignHandoff


def run_experiment_planning_stage(
    run_id: str,
    run_dir: Path,
    stage_dir: Path,
) -> Stage3AcceptanceStageRecord:
    """Run the 3.5 experiment planning stage.

    Consumes 3.4 handoff + benchmark config → produces experiment bundle + handoff.
    """
    from autoad_researcher.experiment.planner import (
        ExperimentPlanner,
        ExperimentPlannerRequest,
        StageResourceEstimateInput,
        StageResourceEstimateProfile,
    )

    handoff_path = stage_dir.parent / "transfer_design" / "idea_transfer_design_handoff.json"
    planner_handoff_path = stage_dir / "experiment_planner_handoff.json"

    # Resume
    if planner_handoff_path.exists():
        handoff_sha = _sha256_file(planner_handoff_path)
        return Stage3AcceptanceStageRecord(
            stage="experiment_planner", status="passed",
            handoff_sha256=handoff_sha,
            artifacts=[
                Stage3AcceptanceArtifactRef(
                    relative_path=str(planner_handoff_path.relative_to(run_dir)),
                    sha256=handoff_sha,
                    artifact_type="experiment_planner_handoff",
                ),
            ],
        )

    if not handoff_path.exists():
        return Stage3AcceptanceStageRecord(
            stage="experiment_planner", status="blocked",
            blocked_reason="blocked_upstream: transfer_design handoff not found",
        )

    # Load 3.4 handoff
    handoff = IdeaTransferDesignHandoff.model_validate_json(
        handoff_path.read_text(encoding="utf-8"),
    )

    # Load repo + env artifacts for planning_input_refs
    planning_refs = _build_planning_refs(run_dir)
    eval_ref = _build_eval_ref(run_dir)
    baseline_config_sha = _load_baseline_config_sha(run_dir)
    seeds, primary_metric, metric_direction = _load_benchmark_config()

    request = ExperimentPlannerRequest(
        handoff=handoff,
        source_handoff_sha256=_sha256_file(handoff_path),
        planning_input_refs=planning_refs,
        supplemental_refs=SupplementalEvaluationRefs(
            evaluator_coverage_evidence_ids=["ev_evaluator"],
            metric_parser_coverage_evidence_ids=["ev_metric_parser"],
            postprocessing_coverage_evidence_ids=["ev_postprocessing"],
            dataset_split_coverage_evidence_ids=["ev_dataset_split"],
        ),
        evaluation_protocol_ref=eval_ref,
        baseline_method="patchcore",
        baseline_config_sha256=baseline_config_sha,
        seeds=seeds,
        primary_metric=primary_metric,
        metric_direction=metric_direction,
        protected_paths=["src/patchcore/metrics.py", "src/patchcore/utils.py"],
        protocol_evidence_ids=["ev_evaluation_contract"],
        decision_rules=_default_decision_rules(),
        resource_limits=ResourceLimits(
            max_total_gpu_hours=24.0,
            max_per_experiment_gpu_hours=4.0,
            available_gpu_count=1,
            available_gpu_type="RTX 4090",
        ),
        resource_estimates=_default_resource_profile(),
    )

    planner = ExperimentPlanner(runs_root=str(run_dir.parent))
    result = planner.run(request)

    if result.validation_report.status != "passed":
        return Stage3AcceptanceStageRecord(
            stage="experiment_planner", status="blocked",
            blocked_reason=f"blocked_validation_failed: {result.validation_report.status}",
        )

    handoff_artifact_path_str = result.artifact_paths.get("experiment_planner_handoff.json")
    if not handoff_artifact_path_str:
        return Stage3AcceptanceStageRecord(
            stage="experiment_planner", status="blocked",
            blocked_reason="blocked_missing_artifact: experiment_planner_handoff.json in planner output",
        )
    handoff_artifact_path = Path(handoff_artifact_path_str)
    handoff_sha = _sha256_file(handoff_artifact_path)
    return Stage3AcceptanceStageRecord(
        stage="experiment_planner", status="passed",
        handoff_sha256=handoff_sha,
        artifacts=[
            Stage3AcceptanceArtifactRef(
                relative_path=str(handoff_artifact_path.relative_to(run_dir)),
                sha256=handoff_sha,
                artifact_type="experiment_planner_handoff",
            ),
        ],
    )


def _build_planning_refs(run_dir: Path) -> PlanningInputRefs:
    repo_source_path = run_dir / "repository_source.json"
    env_context_path = run_dir / "environment_context.json"
    repo_fp = "unknown"
    env_sha = "0" * 64
    if repo_source_path.exists():
        try:
            data = json.loads(repo_source_path.read_text(encoding="utf-8"))
            repo_fp = data.get("source_fingerprint", data.get("tree_sha", "unknown"))
        except Exception:
            pass
    if env_context_path.exists():
        try:
            data = json.loads(env_context_path.read_text(encoding="utf-8"))
            env_sha = _sha256_str(json.dumps(data, sort_keys=True))
        except Exception:
            pass
    return PlanningInputRefs(
        repository_fingerprint=repo_fp,
        environment_sha256=env_sha,
        dataset_manifest_sha256="0" * 64,
        asset_manifest_sha256="0" * 64,
    )


def _build_eval_ref(run_dir: Path) -> ArtifactReferenceV2:
    eval_path = run_dir / "evaluation_contract_draft.json"
    if eval_path.exists():
        sha = _sha256_file(eval_path)
        return ArtifactReferenceV2(
            artifact_id="evaluation_contract",
            artifact_type="evaluation_contract",
            locator=str(eval_path.relative_to(run_dir.parent)),
            sha256=sha,
        )
    return ArtifactReferenceV2(
        artifact_id="evaluation_contract",
        artifact_type="evaluation_contract",
        locator="evaluation_contract_draft.json",
        sha256="0" * 64,
    )


def _load_baseline_config_sha(run_dir: Path) -> str:
    config_path = run_dir / "entrypoints.json"
    if config_path.exists():
        return _sha256_file(config_path)
    return "0" * 64


def _load_benchmark_config() -> tuple[list[int], str, str]:
    return [0], "instance_auroc", "maximize"


def _default_decision_rules() -> list[ScientificDecisionRule]:
    return [
        ScientificDecisionRule(
            rule_id="rule_incomplete", priority=10,
            description="Insufficient completed pairs",
            condition=IncompletePairsCondition(min_pairs=1),
            conclusion_code=ScientificConclusion.INCOMPLETE,
            narrative_template="Insufficient data to draw conclusion.",
        ),
        ScientificDecisionRule(
            rule_id="rule_beneficial", priority=20,
            description="All seeds improved",
            condition=AllSeedsImprovedCondition(),
            conclusion_code=ScientificConclusion.BENEFICIAL,
            narrative_template="Variant improves over baseline on all seeds.",
        ),
        ScientificDecisionRule(
            rule_id="rule_worse", priority=30,
            description="All seeds degraded",
            condition=AllSeedsDegradedCondition(),
            conclusion_code=ScientificConclusion.WORSE,
            narrative_template="Variant degrades compared to baseline on all seeds.",
        ),
        ScientificDecisionRule(
            rule_id="rule_equivalent", priority=40,
            description="Within equivalence margin",
            condition=WithinEquivalenceMarginCondition(margin=0.01),
            conclusion_code=ScientificConclusion.PRACTICALLY_EQUIVALENT,
            narrative_template="Variant is practically equivalent to baseline.",
        ),
        ScientificDecisionRule(
            rule_id="rule_mixed", priority=50,
            description="Mean improvement without unanimous seeds",
            condition=MeanImprovedAboveThresholdCondition(threshold=0.01),
            conclusion_code=ScientificConclusion.MIXED,
            narrative_template="Mixed results across seeds.",
        ),
        ScientificDecisionRule(
            rule_id="rule_always", priority=99,
            description="Catch-all",
            condition=AlwaysCondition(),
            conclusion_code=ScientificConclusion.MIXED,
            narrative_template="Cannot determine conclusion.",
        ),
    ]


def _default_resource_profile() -> "StageResourceEstimateProfile":
    from autoad_researcher.experiment.planner import StageResourceEstimateInput, StageResourceEstimateProfile
    est = StageResourceEstimateInput(
        estimated_gpu_hours_low=0.5, estimated_gpu_hours_high=1.0,
        planning_value=1.0, estimate_source="runtime_default",
        confidence="medium",
    )
    return StageResourceEstimateProfile(baseline=est, fit=est, smoke=est, full=est)


def _sha256_file(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_str(data: str) -> str:
    import hashlib
    return hashlib.sha256(data.encode()).hexdigest()
