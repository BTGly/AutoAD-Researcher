"""Existing Python adapter."""

from autoad_researcher.environments.adapters.base import (
    BaseEnvironmentAdapter,
    EnvironmentAdapterError,
)
from autoad_researcher.environments.models import EnvironmentPlan


class ExistingPythonAdapter(BaseEnvironmentAdapter):
    """Adapter for read-only validation of an existing Python interpreter."""

    kind = "existing_python"

    def validate_target(self, plan: EnvironmentPlan) -> None:
        super().validate_target(plan)
        if plan.target.environment_path is not None:
            raise EnvironmentAdapterError("existing_python target must not set environment_path")
        for step in plan.build_steps:
            if step.network:
                raise EnvironmentAdapterError("existing_python build steps must not use network")
            if step.modifies_repository:
                raise EnvironmentAdapterError("existing_python build steps must not modify repository")
            if _looks_like_install(step.program, step.args):
                raise EnvironmentAdapterError("existing_python build steps must not install packages")


def _looks_like_install(program: str, args: list[str]) -> bool:
    tokens = [program, *args]
    if "install" not in tokens:
        return False
    return any(token in {"pip", "uv", "conda", "mamba", "micromamba"} for token in tokens)
