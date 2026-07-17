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
from autoad_researcher.environments.context_collector import (
    CollectedValidationContext,
    collect_validation_context,
    write_validation_context,
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
from autoad_researcher.environments.probe import (
    HostProbe,
    ProbeCommandResult,
    RepositoryProbe,
    probe_host,
    probe_repository,
    write_probe,
)
from autoad_researcher.environments.policy import (
    EnvironmentPlanPolicyError,
    EnvironmentPlanPolicyReport,
    PolicyViolation,
    evaluate_environment_plan_policy,
    validate_environment_plan_policy,
)
from autoad_researcher.environments.prepare import (
    EnvironmentPreparationError,
    prepare_environment_for_job,
)
from autoad_researcher.environments.result import CommandStepResult, EnvironmentBuildResult
from autoad_researcher.environments.revision import (
    EnvironmentAttemptOutcome,
    EnvironmentRevisionContext,
    EnvironmentRevisionLoopResult,
    build_revision_context,
    run_bounded_revision_loop,
)
from autoad_researcher.environments.snapshot import (
    AcceleratorSnapshot,
    EnvironmentSnapshot,
    InstalledPackage,
    environment_snapshot_sha256,
    snapshot_from_plan,
)
from autoad_researcher.environments.validation import (
    VERIFIERS,
    ValidationContext,
    ValidationReport,
    ValidationResult,
    validate_environment,
)

__all__ = [
    "CommandStep",
    "CollectedValidationContext",
    "EnvironmentPermissions",
    "EnvironmentPlan",
    "EnvironmentPlanRevision",
    "EnvironmentTarget",
    "HostProbe",
    "EnvironmentPlanPolicyError",
    "EnvironmentPlanPolicyReport",
    "EnvironmentPreparationError",
    "CommandExecutionOutput",
    "CommandStepResult",
    "CondaAdapter",
    "AcceleratorSnapshot",
    "EnvironmentSnapshot",
    "EnvironmentAttemptOutcome",
    "EnvironmentBuildResult",
    "EnvironmentAdapter",
    "EnvironmentAdapterError",
    "EnvironmentRevisionContext",
    "EnvironmentRevisionLoopResult",
    "EvidenceReference",
    "ExistingPythonAdapter",
    "InstalledPackage",
    "PipVenvAdapter",
    "PlanAssumption",
    "ProbeCommandResult",
    "PolicyViolation",
    "ResolvedCommand",
    "RepositoryProbe",
    "UvVenvAdapter",
    "ValidationContext",
    "ValidationReport",
    "ValidationResult",
    "ValidationStep",
    "VERIFIERS",
    "build_revision_context",
    "collect_validation_context",
    "environment_plan_sha256",
    "environment_snapshot_sha256",
    "execute_resolved_command",
    "evaluate_environment_plan_policy",
    "get_environment_adapter",
    "load_environment_plan",
    "run_environment_build_steps",
    "run_bounded_revision_loop",
    "snapshot_from_plan",
    "probe_host",
    "probe_repository",
    "prepare_environment_for_job",
    "validate_environment",
    "validate_environment_plan_policy",
    "write_environment_plan",
    "write_probe",
    "write_validation_context",
]
