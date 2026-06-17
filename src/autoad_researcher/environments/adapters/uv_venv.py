"""uv virtual environment adapter."""

from autoad_researcher.environments.adapters.base import (
    BaseEnvironmentAdapter,
    EnvironmentAdapterError,
)
from autoad_researcher.environments.models import EnvironmentPlan


class UvVenvAdapter(BaseEnvironmentAdapter):
    """Adapter for plans targeting uv-managed Python virtual environments."""

    kind = "python_uv_venv"

    def validate_target(self, plan: EnvironmentPlan) -> None:
        super().validate_target(plan)
        if plan.target.environment_path is None:
            raise EnvironmentAdapterError("uv venv target requires environment_path")
        if not any(step.program == "uv" for step in plan.build_steps):
            raise EnvironmentAdapterError("uv venv plan requires at least one uv build step")
