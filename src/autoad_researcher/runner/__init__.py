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

__all__ = [
    "ExperimentCommandPlan",
    "ExperimentExecutionResult",
    "ExperimentInputRefs",
    "OutputManifest",
    "OutputManifestEntry",
    "execute_experiment_attempt",
    "experiment_command_sha256",
    "run_experiment_subprocess",
]
