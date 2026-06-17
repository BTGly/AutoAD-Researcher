"""pip virtual environment adapter."""

from autoad_researcher.environments.adapters.base import (
    BaseEnvironmentAdapter,
    EnvironmentAdapterError,
)
from autoad_researcher.environments.models import EnvironmentPlan


class PipVenvAdapter(BaseEnvironmentAdapter):
    """Adapter for plans targeting stdlib venv plus pip."""

    kind = "python_pip_venv"

    def validate_target(self, plan: EnvironmentPlan) -> None:
        super().validate_target(plan)
        if plan.target.environment_path is None:
            raise EnvironmentAdapterError("pip venv target requires environment_path")
        programs = [step.program for step in plan.build_steps]
        if not any(program == "python" for program in programs):
            raise EnvironmentAdapterError("pip venv plan requires a python build step")
        if not any("pip" in step.args for step in plan.build_steps):
            raise EnvironmentAdapterError("pip venv plan requires a pip build step")
