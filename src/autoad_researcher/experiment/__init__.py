"""Experiment planner module — Step 3.5 Multi-variant Experiment Planner.

Business-logic builders, validators, and emitter live here.
All Pydantic models live in ``schemas/experiment_planning.py`` (repo convention).
"""

from autoad_researcher.experiment.adapter_34 import (
    Stage34HandoffError,
    Stage34InputAdapter,
    compute_unresolved_dimension_id,
    derive_preparation_phase,
)
from autoad_researcher.experiment.builders import (
    ResourceBudgetBuildError,
    ResolutionPlanBuildError,
    build_guard_policy,
    build_resolution_plans,
    build_resource_budget,
)
from autoad_researcher.experiment.matrix_builder import MatrixBuildError, build_matrix
from autoad_researcher.experiment.planner import (
    ExperimentPlanner,
    ExperimentPlannerRequest,
    ExperimentPlannerResult,
    StageResourceEstimateInput,
    StageResourceEstimateProfile,
)
from autoad_researcher.experiment.shared_protocol import build_shared_protocol
from autoad_researcher.experiment.stat_plan import (
    build_stat_plan,
    validate_decision_rule_coverage,
    validate_stat_plan,
)
from autoad_researcher.experiment.trial_specs import build_trial_specs
from autoad_researcher.experiment.validator_emitter import (
    HandoffBlockedError,
    emit_handoff,
    validate_plan,
)

__all__ = [
    "HandoffBlockedError",
    "MatrixBuildError",
    "ResourceBudgetBuildError",
    "ResolutionPlanBuildError",
    "Stage34HandoffError",
    "Stage34InputAdapter",
    "ExperimentPlanner",
    "ExperimentPlannerRequest",
    "ExperimentPlannerResult",
    "StageResourceEstimateInput",
    "StageResourceEstimateProfile",
    "build_guard_policy",
    "build_matrix",
    "build_resolution_plans",
    "build_resource_budget",
    "build_shared_protocol",
    "build_stat_plan",
    "build_trial_specs",
    "compute_unresolved_dimension_id",
    "derive_preparation_phase",
    "emit_handoff",
    "validate_decision_rule_coverage",
    "validate_plan",
    "validate_stat_plan",
]
