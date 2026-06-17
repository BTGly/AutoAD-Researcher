"""Environment adapter registry."""

from autoad_researcher.environments.adapters.base import (
    EnvironmentAdapter,
    EnvironmentAdapterError,
    get_environment_adapter,
)
from autoad_researcher.environments.adapters.conda import CondaAdapter
from autoad_researcher.environments.adapters.existing_python import ExistingPythonAdapter
from autoad_researcher.environments.adapters.pip_venv import PipVenvAdapter
from autoad_researcher.environments.adapters.uv_venv import UvVenvAdapter

__all__ = [
    "EnvironmentAdapter",
    "EnvironmentAdapterError",
    "CondaAdapter",
    "ExistingPythonAdapter",
    "PipVenvAdapter",
    "UvVenvAdapter",
    "get_environment_adapter",
]
