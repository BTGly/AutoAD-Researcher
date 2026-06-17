"""Generic environment planning contracts."""

from autoad_researcher.environments.io import (
    environment_plan_sha256,
    load_environment_plan,
    write_environment_plan,
)
from autoad_researcher.environments.builder import run_environment_build_steps
from autoad_researcher.environments.adapters import (
    CondaAdapter,
    EnvironmentAdapter,
    EnvironmentAdapterError,
    ExistingPythonAdapter,
    PipVenvAdapter,
    UvVenvAdapter,
    get_environment_adapter,
)
from autoad_researcher.environments.executor import (
    CommandExecutionOutput,
    ResolvedCommand,
    execute_resolved_command,
)
from autoad_researcher.environments.models import (
    CommandStep,
    EnvironmentPermissions,
    EnvironmentPlan,
    EnvironmentPlanRevision,
    EnvironmentTarget,
    EvidenceReference,
    PlanAssumption,
    ValidationStep,
)
from autoad_researcher.environments.policy import (
    EnvironmentPlanPolicyError,
    EnvironmentPlanPolicyReport,
    PolicyViolation,
    evaluate_environment_plan_policy,
    validate_environment_plan_policy,
)
from autoad_researcher.environments.result import CommandStepResult, EnvironmentBuildResult
from autoad_researcher.environments.snapshot import (
    AcceleratorSnapshot,
    EnvironmentSnapshot,
    InstalledPackage,
    environment_snapshot_sha256,
    snapshot_from_plan,
)

__all__ = [
    "CommandStep",
    "EnvironmentPermissions",
    "EnvironmentPlan",
    "EnvironmentPlanRevision",
    "EnvironmentTarget",
    "EnvironmentPlanPolicyError",
    "EnvironmentPlanPolicyReport",
    "CommandExecutionOutput",
    "CommandStepResult",
    "CondaAdapter",
    "AcceleratorSnapshot",
    "EnvironmentSnapshot",
    "EnvironmentBuildResult",
    "EnvironmentAdapter",
    "EnvironmentAdapterError",
    "EvidenceReference",
    "ExistingPythonAdapter",
    "InstalledPackage",
    "PipVenvAdapter",
    "PlanAssumption",
    "PolicyViolation",
    "ResolvedCommand",
    "UvVenvAdapter",
    "ValidationStep",
    "environment_plan_sha256",
    "environment_snapshot_sha256",
    "execute_resolved_command",
    "evaluate_environment_plan_policy",
    "get_environment_adapter",
    "load_environment_plan",
    "run_environment_build_steps",
    "snapshot_from_plan",
    "validate_environment_plan_policy",
    "write_environment_plan",
]
