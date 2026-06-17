"""Base adapter interface for environment targets."""

from typing import Protocol

from autoad_researcher.environments.executor import command_from_step
from autoad_researcher.environments.models import EnvironmentPlan
from autoad_researcher.environments.result import ResolvedCommand


class EnvironmentAdapterError(ValueError):
    """Raised when an adapter cannot translate a plan."""


class EnvironmentAdapter(Protocol):
    """Adapter translates a validated EnvironmentPlan into commands."""

    kind: str

    def validate_target(self, plan: EnvironmentPlan) -> None:
        """Validate target-specific invariants."""

    def prepare_steps(self, plan: EnvironmentPlan) -> list[ResolvedCommand]:
        """Translate build_steps into ResolvedCommand objects."""


class BaseEnvironmentAdapter:
    """Common target-kind validation and command translation."""

    kind: str

    def validate_target(self, plan: EnvironmentPlan) -> None:
        if plan.target.kind != self.kind:
            raise EnvironmentAdapterError(
                f"adapter {self.kind!r} cannot handle target kind {plan.target.kind!r}"
            )

    def prepare_steps(self, plan: EnvironmentPlan) -> list[ResolvedCommand]:
        self.validate_target(plan)
        return [command_from_step(step) for step in plan.build_steps]


def get_environment_adapter(kind: str) -> EnvironmentAdapter:
    """Return the adapter for an EnvironmentTarget kind."""
    from autoad_researcher.environments.adapters.conda import CondaAdapter
    from autoad_researcher.environments.adapters.existing_python import (
        ExistingPythonAdapter,
    )
    from autoad_researcher.environments.adapters.pip_venv import PipVenvAdapter
    from autoad_researcher.environments.adapters.uv_venv import UvVenvAdapter

    adapters: dict[str, EnvironmentAdapter] = {
        "python_uv_venv": UvVenvAdapter(),
        "python_pip_venv": PipVenvAdapter(),
        "existing_python": ExistingPythonAdapter(),
        "conda": CondaAdapter(),
    }
    try:
        return adapters[kind]
    except KeyError:
        raise EnvironmentAdapterError(f"unsupported environment adapter: {kind}") from None
