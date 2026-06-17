"""Conda-compatible environment adapter contract."""

from autoad_researcher.environments.adapters.base import (
    BaseEnvironmentAdapter,
    EnvironmentAdapterError,
)
from autoad_researcher.environments.models import EnvironmentPlan

_CONDA_PROGRAMS = {"conda", "mamba", "micromamba"}


class CondaAdapter(BaseEnvironmentAdapter):
    """Adapter for conda/mamba/micromamba plans.

    This adapter only validates and translates explicit plan commands. It does
    not require conda to be installed during CI.
    """

    kind = "conda"

    def validate_target(self, plan: EnvironmentPlan) -> None:
        super().validate_target(plan)
        if plan.target.environment_path is None:
            raise EnvironmentAdapterError("conda target requires environment_path")
        if not any(_program_name(step.program) in _CONDA_PROGRAMS for step in plan.build_steps):
            raise EnvironmentAdapterError("conda plan requires a conda-compatible build step")
        for step in plan.build_steps:
            if _program_name(step.program) in _CONDA_PROGRAMS:
                _validate_conda_args(step.args)


def _program_name(program: str) -> str:
    return program.rsplit("/", 1)[-1]


def _validate_conda_args(args: list[str]) -> None:
    if not args:
        raise EnvironmentAdapterError("conda command requires args")
    allowed_actions = {"create", "env", "install", "run"}
    if args[0] not in allowed_actions:
        raise EnvironmentAdapterError(f"unsupported conda action: {args[0]}")
