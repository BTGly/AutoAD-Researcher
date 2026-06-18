"""Step 3.5 — Multi-variant Experiment Planner schemas.

All Pydantic models defined in strict dependency order (no ``from __future__
import annotations``).  Back-references use forward-reference strings where
needed for discriminated unions.

Schema owner: Step 3.5 Multi-variant Experiment Planner.
Consumer:   Step 3.6 Patch Planner + CommandRequirements.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2

# ---------------------------------------------------------------------------
# 0. Stage 3.5-internal enums (no 3.4 / baseline-arch dependencies)
# ---------------------------------------------------------------------------


class PreparationPhase(str, Enum):
    """Derived from 3.4 regime_changes + state_changes.

    Determines whether a fit intent is required.  Only FIT and TRAIN demand
    a fit stage; INFER_INIT / ONLINE_STATE / NONE skip it.
    """

    FIT = "fit"
    TRAIN = "train"
    INFER_INIT = "infer_init"
    ONLINE_STATE = "online_state"
    NONE = "none"


class ScientificConclusion(str, Enum):
    BENEFICIAL = "beneficial"
    PRACTICALLY_EQUIVALENT = "practically_equivalent"
    MIXED = "mixed"
    WORSE = "worse"
    INCOMPLETE = "incomplete"


class DecisionConditionType(str, Enum):
    ALL_SEEDS_IMPROVED = "all_seeds_improved"
    ALL_SEEDS_DEGRADED = "all_seeds_degraded"
    MEAN_IMPROVED_ABOVE_THRESHOLD = "mean_improved_above_threshold"
    MEAN_DEGRADED_ABOVE_THRESHOLD = "mean_degraded_above_threshold"
    WITHIN_EQUIVALENCE_MARGIN = "within_equivalence_margin"
    MIXED_DIRECTION = "mixed_direction"
    INSUFFICIENT_COMPLETED_PAIRS = "insufficient_completed_pairs"
    ALWAYS = "always"


class ResolutionOutcome(str, Enum):
    RESOLVED_COMPATIBLE = "resolved_compatible"
    RESOLVED_INCOMPATIBLE = "resolved_incompatible"
    REQUIRES_REDESIGN = "requires_redesign"
    INCONCLUSIVE = "inconclusive"


# ---------------------------------------------------------------------------
# 1. SharedExperimentProtocol — 共享实验协议
# ---------------------------------------------------------------------------


class PlanningInputRefs(BaseModel):
    """3.5-阶段可用输入身份引用（不含 command_sha256）。"""

    model_config = ConfigDict(extra="forbid")

    repository_fingerprint: str
    environment_sha256: str
    dataset_manifest_sha256: str
    asset_manifest_sha256: str


class InterfaceConstraint(BaseModel):
    reason: str
    contract_description: str


PLANNING_ARTIFACT_PATHS: tuple[str, ...] = (
    "shared_experiment_protocol.json",
    "statistical_analysis_plan.json",
    "experiment_trial_specs.json",
    "experiment_matrix.json",
    "experimental_resolution_plans.json",
    "resource_budget.json",
    "operational_guard_policy.json",
)


class ValidatedArtifactRef(BaseModel):
    """ValidationReport专用 artifact 引用。路径必须是确定的 7 个 planning artifact 之一。"""

    model_config = ConfigDict(extra="forbid")

    relative_path: Literal[
        "shared_experiment_protocol.json",
        "statistical_analysis_plan.json",
        "experiment_trial_specs.json",
        "experiment_matrix.json",
        "experimental_resolution_plans.json",
        "resource_budget.json",
        "operational_guard_policy.json",
    ]
    sha256: str = Field(pattern=Sha256Pattern)


class SupplementalEvaluationRefs(BaseModel):
    """补充引用：仅在被 PlanningInputRefs 指纹间接覆盖时可为 None。

    覆盖规则（Validator 基于已有 3.0/3.1 artifact 和 EvidenceRef 判断，
    不自行读取仓库文件）。
    """

    evaluator_ref: ArtifactReferenceV2 | None = None
    metric_parser_ref: ArtifactReferenceV2 | None = None
    postprocessing_ref: ArtifactReferenceV2 | None = None
    dataset_split_ref: ArtifactReferenceV2 | None = None

    evaluator_coverage_evidence_ids: list[str] = Field(default_factory=list)
    metric_parser_coverage_evidence_ids: list[str] = Field(default_factory=list)
    postprocessing_coverage_evidence_ids: list[str] = Field(default_factory=list)
    dataset_split_coverage_evidence_ids: list[str] = Field(default_factory=list)


class SeedMetric(BaseModel):
    seed: int
    metric_name: str
    metric_value: float


class BaselineResultRef(BaseModel):
    """引用已存在的 baseline 结果。复用条件：所有身份字段与当前 protocol 完全匹配。"""

    source_run_id: str
    repository_fingerprint: str
    baseline_config_sha256: str
    dataset_manifest_sha256: str
    evaluation_contract_sha256: str
    environment_lock_sha256: str
    asset_manifest_sha256: str
    command_sha256: str
    seeds: list[int]

    per_seed_metrics: list[SeedMetric]

    result_artifact_refs: list[ArtifactReferenceV2]
    validity_report_ref: ArtifactReferenceV2
    validity_status: Literal["valid"]
    completed_seed_ids: list[int]


class BaselineExecutionPolicy(BaseModel):
    """Baseline 执行与复用策略。"""

    mode: Literal["reuse_existing", "run_fresh"]
    reuse_source: BaselineResultRef | None = None
    seeds: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_mode_consistency(self):
        if self.mode == "reuse_existing" and self.reuse_source is None:
            raise ValueError("reuse_existing requires reuse_source")
        if self.mode == "run_fresh" and self.reuse_source is not None:
            raise ValueError("run_fresh must not specify reuse_source")
        return self


class SharedExperimentProtocol(BaseModel):
    """所有 Variant 共享的实验协议。"""

    model_config = ConfigDict(extra="forbid")

    protocol_id: str
    schema_version: Literal[1]
    protocol_version: int = 1

    planning_input_refs: PlanningInputRefs
    supplemental_refs: SupplementalEvaluationRefs
    evaluation_protocol_ref: ArtifactReferenceV2

    baseline_method: str
    baseline_config_sha256: str
    baseline_policy: BaselineExecutionPolicy

    seeds: list[int] = Field(min_length=1)
    primary_metric: str
    metric_direction: Literal["maximize", "minimize"]

    protected_paths: list[str]
    must_not_change: list[InterfaceConstraint]

    protocol_evidence_ids: list[str]
    protocol_fingerprint: str

    @model_validator(mode="after")
    def _validate_seeds(self):
        import math

        if len(self.seeds) != len(set(self.seeds)):
            raise ValueError("seeds must be unique")
        if self.baseline_policy.seeds != self.seeds:
            raise ValueError("baseline_policy.seeds must equal protocol.seeds")
        source = self.baseline_policy.reuse_source
        if source is not None:
            identity_checks = [
                (
                    "repository_fingerprint",
                    source.repository_fingerprint,
                    self.planning_input_refs.repository_fingerprint,
                ),
                (
                    "baseline_config_sha256",
                    source.baseline_config_sha256,
                    self.baseline_config_sha256,
                ),
                (
                    "dataset_manifest_sha256",
                    source.dataset_manifest_sha256,
                    self.planning_input_refs.dataset_manifest_sha256,
                ),
                (
                    "environment_lock_sha256",
                    source.environment_lock_sha256,
                    self.planning_input_refs.environment_sha256,
                ),
                (
                    "asset_manifest_sha256",
                    source.asset_manifest_sha256,
                    self.planning_input_refs.asset_manifest_sha256,
                ),
                (
                    "evaluation_contract_sha256",
                    source.evaluation_contract_sha256,
                    self.evaluation_protocol_ref.sha256,
                ),
            ]
            for field_name, actual, expected in identity_checks:
                if actual != expected:
                    raise ValueError(
                        f"reused baseline {field_name} must match current protocol"
                    )
            if source.seeds != self.seeds:
                raise ValueError("reused baseline seeds must equal protocol seeds")
            if sorted(source.completed_seed_ids) != sorted(self.seeds):
                raise ValueError("reused baseline must complete every protocol seed")
            primary_metrics = [
                m for m in source.per_seed_metrics
                if m.metric_name == self.primary_metric
            ]
            metric_seeds = {m.seed for m in primary_metrics}
            if metric_seeds != set(self.seeds):
                raise ValueError(
                    "reused baseline per_seed_metrics must cover every protocol seed "
                    f"for primary_metric '{self.primary_metric}'"
                )
            if len(primary_metrics) != len(metric_seeds):
                raise ValueError("duplicate seed in per_seed_metrics for primary_metric")
            for m in primary_metrics:
                if m.metric_value is None or math.isnan(m.metric_value) or math.isinf(m.metric_value):
                    raise ValueError(f"seed {m.seed} primary_metric is non-finite")
        return self


# ---------------------------------------------------------------------------
# 2. StatisticalAnalysisPlan — 统计分析计划
# ---------------------------------------------------------------------------


class AlwaysCondition(BaseModel):
    """无条件匹配，必须为最低优先级兜底规则。"""

    model_config = ConfigDict(extra="forbid")
    condition_type: Literal["always"] = "always"


class IncompletePairsCondition(BaseModel):
    """有效种子对数量不足时触发。"""

    model_config = ConfigDict(extra="forbid")
    condition_type: Literal["insufficient_completed_pairs"] = "insufficient_completed_pairs"
    min_pairs: int = 3


class AllSeedsImprovedCondition(BaseModel):
    """所有种子均改善时触发。"""

    model_config = ConfigDict(extra="forbid")
    condition_type: Literal["all_seeds_improved"] = "all_seeds_improved"


class AllSeedsDegradedCondition(BaseModel):
    """所有种子均倒退时触发。"""

    model_config = ConfigDict(extra="forbid")
    condition_type: Literal["all_seeds_degraded"] = "all_seeds_degraded"


class MeanImprovedAboveThresholdCondition(BaseModel):
    """均值改善超过阈值时触发。"""

    model_config = ConfigDict(extra="forbid")
    condition_type: Literal["mean_improved_above_threshold"] = "mean_improved_above_threshold"
    threshold: float = Field(ge=0)


class MeanDegradedAboveThresholdCondition(BaseModel):
    """均值退步超过阈值时触发。"""

    model_config = ConfigDict(extra="forbid")
    condition_type: Literal["mean_degraded_above_threshold"] = "mean_degraded_above_threshold"
    threshold: float = Field(ge=0)


class WithinEquivalenceMarginCondition(BaseModel):
    """均值差在等效区间内时触发。"""

    model_config = ConfigDict(extra="forbid")
    condition_type: Literal["within_equivalence_margin"] = "within_equivalence_margin"
    margin: float = Field(ge=0)


class MixedDirectionCondition(BaseModel):
    """种子间方向不一致时触发。"""

    model_config = ConfigDict(extra="forbid")
    condition_type: Literal["mixed_direction"] = "mixed_direction"


DecisionConditionUnion = Annotated[
    Union[
        AlwaysCondition,
        IncompletePairsCondition,
        AllSeedsImprovedCondition,
        AllSeedsDegradedCondition,
        MeanImprovedAboveThresholdCondition,
        MeanDegradedAboveThresholdCondition,
        WithinEquivalenceMarginCondition,
        MixedDirectionCondition,
    ],
    Field(discriminator="condition_type"),
]


class ScientificDecisionRule(BaseModel):
    """一条确定性判定规则。"""

    rule_id: str
    priority: int
    description: str
    condition: DecisionConditionUnion = Field(discriminator="condition_type")  # type: ignore[assignment]
    conclusion_code: ScientificConclusion
    narrative_template: str


class StatisticalAnalysisPlan(BaseModel):
    """运行前固定的统计分析计划。独立文件，单独计算 SHA。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    schema_version: Literal[1]
    protocol_fingerprint: str

    primary_metric: str
    metric_direction: Literal["maximize", "minimize"]
    metric_scale: str | None = None

    aggregation: Literal["mean", "median"]
    dispersion: Literal["std", "iqr"]
    paired_by_seed: bool = True

    minimum_meaningful_effect: float | None = None
    minimum_meaningful_effect_source: str | None = None
    minimum_meaningful_effect_evidence_ids: list[str] = Field(default_factory=list)
    user_confirmation_evidence_id: str | None = None

    missing_run_policy: Literal[
        "invalidate_variant",
        "rerun_failed_seed_once",
        "report_incomplete",
    ]
    max_rerun_attempts: int = 1

    multiple_variant_policy: Literal[
        "descriptive_only",
        "rank_by_primary_metric",
    ]

    decision_rules: list[ScientificDecisionRule] = Field(min_length=1)
    plan_fingerprint: str


# ---------------------------------------------------------------------------
# 3. ExperimentTrialSpecs — 试验规格
# ---------------------------------------------------------------------------


class ArtifactRequirement(BaseModel):
    requirement_id: str
    artifact_type: Literal[
        "model_weights",
        "config_file",
        "dataset_split",
        "evaluation_results",
        "training_log",
        "metrics_json",
    ]
    description: str
    format_hint: str | None = None


class ArtifactProduction(BaseModel):
    """一个实验 entry 产生的确定 artifact。"""

    production_id: str
    artifact_type: Literal[
        "model_weights", "checkpoint", "metrics_json",
        "predictions", "embeddings", "logs",
    ]
    description: str


class TrialIntent(BaseModel):
    """实验意图：声明做什么，不写具体命令。

    不含 depends_on、timeout_policy_ref、shell 命令。
    """

    intent_id: str
    intent_type: Literal[
        "baseline_run",
        "variant_fit",
        "smoke_inference",
        "full_evaluation",
    ]
    description: str

    required_inputs: list[ArtifactRequirement]
    expected_outputs: list[ArtifactRequirement]
    # 权威 production spec：MatrixBuilder 从此派生 ArtifactProduction，不手工创建
    # ArtifactRequirement.requirement_id → ArtifactProduction.production_id


class DependencyRequirement(BaseModel):
    package_name: str
    version_spec: str | None = None
    reason: str


class AssetRequirement(BaseModel):
    asset_description: str
    expected_source: Literal["torchvision_hub", "huggingface", "user_provided", "url"]
    expected_sha256: str | None = None
    required: bool = True


class AcceleratorRequirement(BaseModel):
    gpu_required: bool
    min_vram_gb: float | None = None
    gpu_type_preference: str | None = None


class VariantImplementationRequirements(BaseModel):
    dependency_deltas: list[DependencyRequirement]
    asset_requirements: list[AssetRequirement]
    accelerator_requirements: AcceleratorRequirement
    environment_rebuild_required: bool


class HyperparameterSpec(BaseModel):
    name: str
    value: object
    source: Literal["paper_default", "user_specified", "baseline_inherited"]
    rationale: str | None = None


class DatasetPartitionRef(BaseModel):
    """引用 dataset manifest 中已验证的 partition。"""

    partition_id: str
    dataset_manifest_sha256: str
    declared_role: Literal["train", "validation", "test"]
    evidence_ids: list[str]


class SearchBudget(BaseModel):
    max_trials: int
    max_gpu_hours: float


class SearchParameter(BaseModel):
    name: str
    type: Literal["float", "int", "categorical"]
    range: list[object] | tuple[float, float]


class HyperparameterPlan(BaseModel):
    """Hyperparameter 规划，防止测试集泄漏。"""

    mode: Literal["fixed_from_source", "fixed_by_user", "predeclared_search"]
    source_evidence_ids: list[str]

    parameters: list[HyperparameterSpec] = Field(default_factory=list)

    search_space: list[SearchParameter] | None = None
    selection_split: DatasetPartitionRef | None = None
    search_budget: SearchBudget | None = None
    selection_metric: str | None = None


class VariantTrialSpec(BaseModel):
    """单个 Variant 的试验规格。"""

    variant_id: str
    variant_label: str
    idea_id: str

    primary_hook_id: str
    hook_bindings: list  # HookBinding from schemas/transfer_design.py
    interface_deltas: list  # InterfaceContractDelta
    regime_changes: list  # RegimeChange
    state_changes: list  # StateChangeDescription
    adapter_required: bool
    new_dependencies: list[str]
    risk_level: Literal["low", "medium", "high"]

    preparation_phase: PreparationPhase

    fit: TrialIntent | None = None
    fit_seed_policy: Literal["shared_fixed", "per_evaluation_seed", "deterministic_no_seed"] | None = None

    @model_validator(mode="after")
    def _validate_fit_consistency(self):
        needs_fit = self.preparation_phase in {PreparationPhase.FIT, PreparationPhase.TRAIN}
        if needs_fit and self.fit is None:
            raise ValueError("preparation_phase requires fit but fit intent is None")
        if needs_fit and self.fit_seed_policy is None:
            raise ValueError("fit intent present but fit_seed_policy is None")
        if not needs_fit and self.fit is not None:
            raise ValueError("fit intent present but preparation_phase does not require fit")
        if not needs_fit and self.fit_seed_policy is not None:
            raise ValueError("fit_seed_policy set but no fit intent")
        return self

    smoke: TrialIntent
    full: TrialIntent

    implementation_requirements: VariantImplementationRequirements
    hyperparameter_plan: HyperparameterPlan
    evidence_ids: list[str]


class ExperimentTrialSpecs(BaseModel):
    """所有试验规格。单文件，Variant 仅 1–3 个。"""

    model_config = ConfigDict(extra="forbid")

    specs_id: str
    schema_version: Literal[1]
    protocol_fingerprint: str

    baseline: TrialIntent | None = None
    variants: list[VariantTrialSpec] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _validate_unique_ids(self):
        vids = [v.variant_id for v in self.variants]
        if len(vids) != len(set(vids)):
            raise ValueError("duplicate variant_id in ExperimentTrialSpecs")
        return self


# ---------------------------------------------------------------------------
# 4. ExperimentMatrix — 实验矩阵
# ---------------------------------------------------------------------------


class MatrixInputBinding(BaseModel):
    """表达 artifact 产物来源（精确到生产 ID）。"""

    consumer_entry_id: str
    consumer_requirement_id: str
    producer_entry_id: str
    producer_production_id: str


class MatrixEntry(BaseModel):
    entry_id: str
    variant_id: str | None
    stage: Literal["baseline", "fit", "smoke", "full"]
    seed: int | None

    intent_ref: str

    depends_on: list[str]
    expected_outputs: list[ArtifactProduction] = Field(default_factory=list)
    # 从 TrialIntent.expected_outputs 派生，不手工创建
    # ArtifactRequirement.requirement_id → ArtifactProduction.production_id
    # ArtifactRequirement.artifact_type → ArtifactProduction.artifact_type
    # ArtifactRequirement.description  → ArtifactProduction.description

    shared_axes: list[str]
    independent_axes: list[str]
    priority: int = 0


class ExperimentMatrix(BaseModel):
    """baseline × seeds + variant × (fit + smoke) + variant × seeds 矩阵。"""

    model_config = ConfigDict(extra="forbid")

    matrix_id: str
    schema_version: Literal[1]
    protocol_fingerprint: str

    seeds: list[int]
    variants: list[str]

    entries: list[MatrixEntry]
    input_bindings: list[MatrixInputBinding] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 5. ExperimentalResolutionPlans — 实验解析计划
# ---------------------------------------------------------------------------


class ThresholdCriterion(BaseModel):
    """值高于/低于阈值。"""

    model_config = ConfigDict(extra="forbid")
    criterion_type: Literal["value_above_threshold", "value_below_threshold"]
    metric_name: str
    threshold: float
    unit: str | None = None


class RangeCriterion(BaseModel):
    """值在区间内。"""

    model_config = ConfigDict(extra="forbid")
    criterion_type: Literal["value_in_range"] = "value_in_range"
    metric_name: str
    lower_bound: float
    upper_bound: float
    inclusive: bool = True

    @model_validator(mode="after")
    def _validate_range(self):
        if self.lower_bound > self.upper_bound:
            raise ValueError("lower_bound must be <= upper_bound")
        return self


class NoNanCriterion(BaseModel):
    """无 NaN/Inf。"""

    model_config = ConfigDict(extra="forbid")
    criterion_type: Literal["no_nan_detected"] = "no_nan_detected"


class ShapeMatchCriterion(BaseModel):
    """输出 shape 匹配 baseline。"""

    model_config = ConfigDict(extra="forbid")
    criterion_type: Literal["output_shape_matches_baseline"] = "output_shape_matches_baseline"
    expected_tensor_contract_ref: str
    baseline_output_shape_ref: str


ResolutionCriterion = Annotated[
    Union[
        ThresholdCriterion,
        RangeCriterion,
        NoNanCriterion,
        ShapeMatchCriterion,
    ],
    Field(discriminator="criterion_type"),
]


class ExperimentalResolutionPlan(BaseModel):
    unresolved_dimension_id: str
    dimension: str
    variant_id: str
    verification_stage: Literal["fit", "smoke", "full"]

    target_entry_ids: list[str]

    observable: str
    observation_source: str

    acceptance_criterion: ResolutionCriterion
    rejection_criterion: ResolutionCriterion | None = None

    result_on_accept: ResolutionOutcome
    result_on_reject: ResolutionOutcome | None = None
    result_on_inconclusive: ResolutionOutcome = ResolutionOutcome.INCONCLUSIVE

    @model_validator(mode="after")
    def _validate_outcomes(self):
        if (self.rejection_criterion is None) != (self.result_on_reject is None):
            raise ValueError("rejection_criterion and result_on_reject must appear together")
        if self.result_on_accept == ResolutionOutcome.INCONCLUSIVE:
            raise ValueError("accept branch cannot resolve to inconclusive")
        return self


class ExperimentalResolutionPlans(BaseModel):
    """将 3.4 的未解决问题编译为可执行的验证步骤。"""

    model_config = ConfigDict(extra="forbid")

    plans_id: str
    schema_version: Literal[1]
    protocol_fingerprint: str

    resolutions: list[ExperimentalResolutionPlan]


# ---------------------------------------------------------------------------
# 6. ResourceBudget + BudgetDecision — 资源预算与决策
# ---------------------------------------------------------------------------


class ResourceLimits(BaseModel):
    max_total_gpu_hours: float = Field(ge=0)
    max_per_experiment_gpu_hours: float = Field(ge=0)
    available_gpu_count: int = Field(ge=1)
    available_gpu_type: str


class EntryResourceEstimate(BaseModel):
    """单个 matrix entry 的资源估算。通过 entry_id 关联 Matrix。"""

    model_config = ConfigDict(extra="forbid")

    entry_id: str
    estimated_gpu_hours_low: float = Field(ge=0)
    estimated_gpu_hours_high: float = Field(ge=0)
    planning_value: float = Field(ge=0)
    safety_factor: float = Field(default=1.0, ge=1.0)
    estimated_peak_gpu_memory_gb: float | None = Field(default=None, ge=0)
    estimated_disk_gb: float = Field(default=0.0, ge=0)
    estimate_source: str
    confidence: Literal["high", "medium", "low"]
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_estimate_consistency(self):
        if self.estimated_gpu_hours_low > self.estimated_gpu_hours_high:
            raise ValueError("low estimate must be <= high estimate")
        if self.confidence == "low":
            minimum = self.estimated_gpu_hours_high * self.safety_factor
            if self.planning_value < minimum:
                raise ValueError(
                    f"planning_value ({self.planning_value}) must be >= "
                    f"high × safety_factor = {minimum}"
                )
        return self


class VariantResourceSummary(BaseModel):
    variant_id: str | None
    entries: list[EntryResourceEstimate]
    total_gpu_hours: float
    total_wall_clock_hours: float


class ExperimentBundleResourceBudget(BaseModel):
    total_gpu_hours: float
    total_wall_clock_hours: float
    max_single_experiment_gpu_hours: float


class BudgetRevisionOption(BaseModel):
    option_id: str
    description: str
    savings_gpu_hours: float
    new_total_gpu_hours: float
    affects_seeds: bool
    affects_variants: bool
    affects_stages: bool
    tradeoffs: list[str]


class BudgetDecision(BaseModel):
    """预算决策状态机。"""

    status: Literal[
        "within_budget",
        "revision_required",
        "revision_selected",
        "override_pending",
        "override_confirmed",
        "override_rejected",
    ]

    original_limits: ResourceLimits
    estimated_consumption: ExperimentBundleResourceBudget
    utilization_pct: float
    over_budget_items: list[str] = Field(default_factory=list)

    revision_options: list[BudgetRevisionOption] = Field(default_factory=list)
    selected_revision_option_id: str | None = None

    approved_limits: ResourceLimits | None = None
    user_decision_evidence_id: str | None = None
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_state(self):
        if self.status == "revision_selected":
            if not self.selected_revision_option_id:
                raise ValueError("revision_selected requires selected_revision_option_id")
            valid_ids = {o.option_id for o in self.revision_options}
            if self.selected_revision_option_id not in valid_ids:
                raise ValueError("selected revision option does not exist")
        if self.status == "override_confirmed":
            if self.approved_limits is None:
                raise ValueError("override_confirmed requires approved_limits")
            if not self.user_decision_evidence_id:
                raise ValueError("override_confirmed requires user_decision_evidence_id")
            if self.decided_at is None:
                raise ValueError("override_confirmed requires decided_at")
        if self.status in ("override_rejected",):
            if not self.user_decision_evidence_id:
                raise ValueError(f"{self.status} requires user_decision_evidence_id")
        if self.status == "within_budget" and self.over_budget_items:
            raise ValueError("within_budget cannot contain over_budget_items")
        return self


class ResourceBudget(BaseModel):
    """资源预算汇总 + 预算决策。"""

    model_config = ConfigDict(extra="forbid")

    budget_id: str
    schema_version: Literal[1]
    protocol_fingerprint: str
    protocol_version: int

    limits: ResourceLimits
    per_variant: dict[str, VariantResourceSummary]
    total_estimate: ExperimentBundleResourceBudget
    budget_decision: BudgetDecision


# ---------------------------------------------------------------------------
# 7. OperationalGuardPolicy — 运行保护策略
# ---------------------------------------------------------------------------


class OperationalGuard(BaseModel):
    guard_id: str
    guard_type: Literal[
        "timeout",
        "oom_detected",
        "nan_inf_detected",
        "crash_detected",
        "disk_exhaustion",
        "global_resource_cap",
    ]
    target_entry_ids: list[str]
    parameters: dict[str, object]
    action: Literal["stop_entry", "stop_variant", "stop_all"]
    is_blocking: bool


class OperationalGuardPolicy(BaseModel):
    """运行保护策略：处理崩溃、超时、资源耗尽等运行安全问题。"""

    model_config = ConfigDict(extra="forbid")

    policy_id: str
    schema_version: Literal[1]
    protocol_fingerprint: str

    guards: list[OperationalGuard]


# ---------------------------------------------------------------------------
# 8. ValidationReport — 确定性计划验证
# ---------------------------------------------------------------------------


class ExperimentPlanValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    severity: Literal["blocking", "warning"]
    invariant_category: Literal[
        "structure",
        "baseline_fairness",
        "statistics",
        "hyperparameter_safety",
        "cross_variant_consistency",
        "resolution_coverage",
        "budget",
        "evaluation_chain",
        "dependency_dag",
        "uniqueness",
    ]
    message: str
    artifact_refs: list[ArtifactReferenceV2] = Field(default_factory=list)


class InvariantResult(BaseModel):
    category: str
    passed: bool
    issue_ids: list[str] = Field(default_factory=list)


class ExperimentPlanValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    run_id: str
    protocol_fingerprint: str
    status: Literal["passed", "failed"]
    invariant_results: list[InvariantResult] = Field(default_factory=list)
    issues: list[ExperimentPlanValidationIssue] = Field(default_factory=list)
    validated_artifact_refs: list[ValidatedArtifactRef] = Field(default_factory=list)
    validated_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_status(self):
        has_blocking = any(i.severity == "blocking" for i in self.issues)
        has_failed = any(not r.passed for r in self.invariant_results)
        expected = "failed" if (has_blocking or has_failed) else "passed"
        if self.status != expected:
            raise ValueError(
                f"status {self.status} inconsistent: "
                f"blocking={has_blocking}, failed_invariants={has_failed}"
            )
        return self

    @model_validator(mode="after")
    def _validate_artifact_refs(self):
        paths = [r.relative_path for r in self.validated_artifact_refs]
        if sorted(paths) != sorted(PLANNING_ARTIFACT_PATHS):
            raise ValueError(
                "validated_artifact_refs must contain exactly the 7 planning artifacts, "
                f"got {sorted(paths)}"
            )
        if len(paths) != len(set(paths)):
            raise ValueError("validated_artifact_refs must have unique relative_path")
        return self


# ---------------------------------------------------------------------------
# 9. Handoff — 3.5 → 3.6
# ---------------------------------------------------------------------------


class ManifestEntry(BaseModel):
    relative_path: str
    sha256: str
    artifact_type: str

    @field_validator("relative_path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        from pathlib import PurePosixPath

        if ".." in PurePosixPath(v).parts:
            raise ValueError("parent traversal forbidden")
        if PurePosixPath(v).is_absolute():
            raise ValueError("absolute path forbidden")
        return v


class ArtifactManifest(BaseModel):
    entries: list[ManifestEntry]


class ExperimentPlannerHandoff(BaseModel):
    """3.5 → 3.6 handoff。SHA-only 引用。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: str
    source_handoff_sha256: str
    artifact_manifest: ArtifactManifest
    selected_variant_ids: list[str]
    validation_report_sha256: str
    next_stage: Literal["3.6_patch_planner"]


# ---------------------------------------------------------------------------
# 10. Adapter internal types — 3.4 → 3.5 输入
# ---------------------------------------------------------------------------

# Import 3.4 sealed types at the end to avoid circular dependency during
# module-level resolution (these are only used by schemas defined below).

from autoad_researcher.schemas.transfer_design import (  # noqa: E402
    IdeaContract,
    IdeaTransferAnalysis,
    ImplementationVariant,
    TransferConstraint,
    UnresolvedDimension,
    VariantRiskReport,
    VariantTransferAnalysis,
)


class Stage35VariantInput(BaseModel):
    """Adapter 输出的单个 variant 输入。"""

    variant: ImplementationVariant
    transfer_analysis: VariantTransferAnalysis
    risk_report: VariantRiskReport
    experiment_resolvable: list[UnresolvedDimension]


class Stage35Input(BaseModel):
    """3.4 → 3.5 adapter 输出。"""

    run_id: str
    confirmed_idea: IdeaContract
    transfer_analysis: IdeaTransferAnalysis
    transfer_constraints: list[TransferConstraint]
    variants: list[Stage35VariantInput]
    nonblocking_warnings: list[UnresolvedDimension]
