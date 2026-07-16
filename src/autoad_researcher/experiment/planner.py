"""Production orchestrator for Step 3.5 Multi-variant Experiment Planner."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.experiment.builders import (
    build_guard_policy,
    build_resolution_plans,
    build_resource_budget,
)
from autoad_researcher.experiment.matrix_builder import build_matrix
from autoad_researcher.experiment.shared_protocol import build_shared_protocol
from autoad_researcher.experiment.stat_plan import build_stat_plan
from autoad_researcher.experiment.trial_specs import build_trial_specs
from autoad_researcher.experiment.validator_emitter import emit_handoff, validate_plan
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.experiment_planning import (
    PLANNING_ARTIFACT_PATHS,
    BaselineExecutionPolicy,
    EntryResourceEstimate,
    ExperimentPlanValidationReport,
    ExperimentPlanningInput,
    ExperimentPlannerHandoff,
    InterfaceConstraint,
    MatrixEntry,
    PlanningInputRefs,
    ResourceLimits,
    ScientificDecisionRule,
    SharedExperimentProtocol,
    SupplementalEvaluationRefs,
)


class StageResourceEstimateInput(BaseModel):
    """User-provided resource estimate template for one MatrixEntry stage."""

    model_config = ConfigDict(extra="forbid")

    estimated_gpu_hours_low: float = Field(ge=0)
    estimated_gpu_hours_high: float = Field(ge=0)
    planning_value: float = Field(ge=0)
    safety_factor: float = Field(default=1.0, ge=1.0)
    estimated_peak_gpu_memory_gb: float | None = Field(default=None, ge=0)
    estimated_disk_gb: float = Field(default=0.0, ge=0)
    estimate_source: str
    confidence: str
    assumptions: list[str] = Field(default_factory=list)


class StageResourceEstimateProfile(BaseModel):
    """Resource estimates keyed by MatrixEntry.stage."""

    model_config = ConfigDict(extra="forbid")

    baseline: StageResourceEstimateInput
    fit: StageResourceEstimateInput
    smoke: StageResourceEstimateInput
    full: StageResourceEstimateInput

    def estimate_for_entry(self, entry: MatrixEntry) -> EntryResourceEstimate:
        template = getattr(self, entry.stage)
        return EntryResourceEstimate(
            entry_id=entry.entry_id,
            estimated_gpu_hours_low=template.estimated_gpu_hours_low,
            estimated_gpu_hours_high=template.estimated_gpu_hours_high,
            planning_value=template.planning_value,
            safety_factor=template.safety_factor,
            estimated_peak_gpu_memory_gb=template.estimated_peak_gpu_memory_gb,
            estimated_disk_gb=template.estimated_disk_gb,
            estimate_source=template.estimate_source,
            confidence=template.confidence,  # type: ignore[arg-type]
            assumptions=template.assumptions,
        )


class ExperimentPlannerRequest(BaseModel):
    """All confirmed inputs needed to generate an experiment plan."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    planning_input: ExperimentPlanningInput
    source_input_sha256: str
    planning_input_refs: PlanningInputRefs
    supplemental_refs: SupplementalEvaluationRefs
    evaluation_protocol_ref: ArtifactReferenceV2
    baseline_method: str
    baseline_config_sha256: str
    seeds: list[int] = Field(min_length=1)
    primary_metric: str
    metric_direction: str
    protected_paths: list[str] = Field(default_factory=list)
    must_not_change: list[InterfaceConstraint] = Field(default_factory=list)
    protocol_evidence_ids: list[str] = Field(default_factory=list)
    decision_rules: list[ScientificDecisionRule] = Field(min_length=1)
    resource_limits: ResourceLimits
    resource_estimates: StageResourceEstimateProfile
    baseline_policy: BaselineExecutionPolicy | None = None


class ExperimentPlannerResult(BaseModel):
    """Step 3.5 run result with paths to generated artifacts."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    artifact_dir: str
    artifact_paths: dict[str, str]
    validation_report: ExperimentPlanValidationReport
    handoff: ExperimentPlannerHandoff


class ExperimentPlanner:
    """Compile a confirmed, source-neutral input into planning artifacts."""

    def __init__(self, runs_root: str | Path = "runs") -> None:
        self._runs_root = Path(runs_root)

    def run(self, request: ExperimentPlannerRequest) -> ExperimentPlannerResult:
        run_id = request.planning_input.run_id
        run_dir = run_dir_path(self._runs_root, run_id)
        artifact_dir = run_dir / "experiment_planning"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        protocol = build_shared_protocol(
            planning_input_refs=request.planning_input_refs,
            supplemental_refs=request.supplemental_refs,
            evaluation_protocol_ref=request.evaluation_protocol_ref,
            baseline_method=request.baseline_method,
            baseline_config_sha256=request.baseline_config_sha256,
            seeds=request.seeds,
            primary_metric=request.primary_metric,
            metric_direction=request.metric_direction,  # type: ignore[arg-type]
            protected_paths=request.protected_paths,
            must_not_change=request.must_not_change,
            protocol_evidence_ids=request.protocol_evidence_ids,
            baseline_policy=request.baseline_policy,
        )
        stat_plan = build_stat_plan(
            protocol_fingerprint=protocol.protocol_fingerprint,
            primary_metric=protocol.primary_metric,
            metric_direction=protocol.metric_direction,
            aggregation="mean",
            dispersion="std",
            missing_run_policy="report_incomplete",
            multiple_variant_policy="descriptive_only",
            decision_rules=request.decision_rules,
        )
        specs = build_trial_specs(request.planning_input, protocol.protocol_fingerprint)
        matrix = build_matrix(protocol, specs)
        resolution_plans = build_resolution_plans(
            request.planning_input,
            matrix,
            protocol.protocol_fingerprint,
        )
        entry_estimates = [
            request.resource_estimates.estimate_for_entry(entry)
            for entry in matrix.entries
        ]
        budget = build_resource_budget(
            matrix=matrix,
            protocol_fingerprint=protocol.protocol_fingerprint,
            protocol_version=protocol.protocol_version,
            limits=request.resource_limits,
            entry_estimates=entry_estimates,
        )
        guard_policy = build_guard_policy(
            matrix=matrix,
            protocol_fingerprint=protocol.protocol_fingerprint,
            resource_budget=budget,
        )

        artifacts = {
            "shared_experiment_protocol.json": protocol,
            "statistical_analysis_plan.json": stat_plan,
            "experiment_trial_specs.json": specs,
            "experiment_matrix.json": matrix,
            "experimental_resolution_plans.json": resolution_plans,
            "resource_budget.json": budget,
            "operational_guard_policy.json": guard_policy,
        }
        artifact_paths = {
            name: artifact_dir / name
            for name in PLANNING_ARTIFACT_PATHS
        }
        for name, artifact in artifacts.items():
            _write_model(artifact_paths[name], artifact)

        validation_report = validate_plan(artifact_paths, run_id=run_id)
        report_path = artifact_dir / "experiment_plan_validation_report.json"
        _write_model(report_path, validation_report)
        emit_paths = {**artifact_paths, report_path.name: report_path}
        handoff = emit_handoff(
            emit_paths,
            run_id=run_id,
            source_sha256=request.source_input_sha256,
        )
        handoff_path = artifact_dir / "experiment_planner_handoff.json"
        _write_model(handoff_path, handoff)

        all_paths = {
            **{name: str(path) for name, path in artifact_paths.items()},
            report_path.name: str(report_path),
            handoff_path.name: str(handoff_path),
        }
        return ExperimentPlannerResult(
            run_id=run_id,
            artifact_dir=str(artifact_dir),
            artifact_paths=all_paths,
            validation_report=validation_report,
            handoff=handoff,
        )


def _write_model(path: Path, model: BaseModel) -> None:
    path.write_text(
        json.dumps(model.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
