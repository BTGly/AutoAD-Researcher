"""Experiment planner module — Step 3.5 Multi-variant Experiment Planner.

Business-logic builders, validators, and emitter live here.
All Pydantic models live in ``schemas/experiment_planning.py`` (repo convention).
"""

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
    "emit_handoff",
    "validate_decision_rule_coverage",
    "validate_plan",
    "validate_stat_plan",
]
"""Experiment control-plane contracts and durable stores."""

from autoad_researcher.experiment.attempt import ExperimentAttempt
from autoad_researcher.experiment.attempt_service import ExperimentAttemptService
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.gpu import GpuAllocator, ResourceLease
from autoad_researcher.experiment.watchdog import RuntimeWatchdog

__all__ = ["ExperimentAttempt", "ExperimentAttemptService", "ExperimentAttemptStore", "GpuAllocator", "ResourceLease", "RuntimeWatchdog"]
