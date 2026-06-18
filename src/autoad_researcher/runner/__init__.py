"""Controlled experiment runner.

Principles:
- use structured commands, not shell strings;
- never overwrite an existing attempt directory;
- save stdout, stderr, output manifest, and execution result;
- keep experiment execution network disabled.
"""

from autoad_researcher.runner.executor import (
    execute_experiment_attempt,
    experiment_command_sha256,
    run_experiment_subprocess,
)
from autoad_researcher.runner.models import (
    ExperimentCommandPlan,
    ExperimentExecutionResult,
    ExperimentInputRefs,
    OutputManifest,
    OutputManifestEntry,
)
from autoad_researcher.runner.validators import (
    compute_identity_match,
    derive_attempt_outcome,
    derive_execution_status,
    derive_final_status,
    derive_overall_status,
    derive_terminal_reason,
    derive_workspace_execution_refs,
    validate_attempt_record_against_artifacts,
    validate_handoff_against_manifest,
    validate_intake_against_patch_handoff,
    validate_resolution_presence,
)

__all__ = [
    "ExperimentCommandPlan",
    "ExperimentExecutionResult",
    "ExperimentInputRefs",
    "OutputManifest",
    "OutputManifestEntry",
    "execute_experiment_attempt",
    "experiment_command_sha256",
    "run_experiment_subprocess",
    # Step 3.8 validators
    "compute_identity_match",
    "derive_attempt_outcome",
    "derive_execution_status",
    "derive_final_status",
    "derive_overall_status",
    "derive_terminal_reason",
    "derive_workspace_execution_refs",
    "validate_attempt_record_against_artifacts",
    "validate_handoff_against_manifest",
    "validate_intake_against_patch_handoff",
    "validate_resolution_presence",
]
