"""Policy gate for generic EnvironmentPlan objects."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.environments.io import environment_plan_sha256
from autoad_researcher.environments.models import (
    CommandStep,
    EnvironmentPlan,
    PlanAssumption,
    ValidationStep,
)

FORBIDDEN_PROGRAMS = {
    "apt",
    "apt-get",
    "apk",
    "brew",
    "dnf",
    "pacman",
    "rpm",
    "su",
    "sudo",
    "yum",
}

SYSTEM_INSTALL_PROGRAMS = {
    "apt",
    "apt-get",
    "apk",
    "brew",
    "dnf",
    "pacman",
    "yum",
}

FORBIDDEN_ARG_TOKENS = {
    "|",
    ">",
    "<",
    "&&",
    "||",
    ";",
    "`",
    "$(",
}

ALLOWED_ENVIRONMENT_KEYS = {
    "CUDA_VISIBLE_DEVICES",
    "HF_HOME",
    "PIP_EXTRA_INDEX_URL",
    "PIP_INDEX_URL",
    "PYTHONPATH",
    "TORCH_HOME",
    "UV_EXTRA_INDEX_URL",
    "UV_INDEX_URL",
    "UV_LINK_MODE",
    "XDG_CACHE_HOME",
}


class PolicyViolation(BaseModel):
    """One policy denial reason."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(pattern=r"^ENV_[A-Z0-9_]+$")
    location: str = Field(min_length=1)
    message: str = Field(min_length=1)


class EnvironmentPlanPolicyReport(BaseModel):
    """Deterministic report emitted by the policy gate."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    plan_id: str
    run_id: str
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["passed", "denied"]
    violations: list[PolicyViolation] = Field(default_factory=list)


class EnvironmentPlanPolicyError(ValueError):
    """Raised when a plan fails the policy gate."""

    def __init__(self, report: EnvironmentPlanPolicyReport):
        self.report = report
        messages = "; ".join(v.message for v in report.violations)
        super().__init__(messages or "environment plan policy denied")


def evaluate_environment_plan_policy(plan: EnvironmentPlan) -> EnvironmentPlanPolicyReport:
    """Evaluate a plan and return a report without raising."""
    violations: list[PolicyViolation] = []

    _check_target(plan, violations)
    _check_unique_ids(plan, violations)
    _check_required_validation(plan.validation_steps, violations)

    validation_ids = {step.validation_id for step in plan.validation_steps}
    for i, assumption in enumerate(plan.assumptions):
        _check_assumption(assumption, validation_ids, i, violations)

    for i, step in enumerate(plan.build_steps):
        _check_command_step(plan, step, i, violations)

    for i, step in enumerate(plan.validation_steps):
        _check_validation_step(step, i, violations)

    status: Literal["passed", "denied"] = "denied" if violations else "passed"
    return EnvironmentPlanPolicyReport(
        schema_version=1,
        plan_id=plan.plan_id,
        run_id=plan.run_id,
        plan_sha256=environment_plan_sha256(plan),
        status=status,
        violations=violations,
    )


def validate_environment_plan_policy(plan: EnvironmentPlan) -> EnvironmentPlanPolicyReport:
    """Evaluate a plan and raise EnvironmentPlanPolicyError if denied."""
    report = evaluate_environment_plan_policy(plan)
    if report.status == "denied":
        raise EnvironmentPlanPolicyError(report)
    return report


def _check_target(plan: EnvironmentPlan, violations: list[PolicyViolation]) -> None:
    target = plan.target
    if target.environment_path is not None:
        _require_safe_path(
            target.environment_path,
            "target.environment_path",
            violations,
            code="ENV_PATH_OUTSIDE_WORKSPACE",
        )
        if not _path_has_prefix(target.environment_path, ("workspace", "envs")):
            _violate(
                violations,
                "ENV_PATH_OUTSIDE_WORKSPACE",
                "target.environment_path",
                "environment_path must be under workspace/envs",
            )
    elif target.kind != "existing_python":
        _violate(
            violations,
            "ENV_PLAN_POLICY_DENIED",
            "target.environment_path",
            "environment_path is required unless kind is existing_python",
        )

    if target.repository_path is not None:
        _require_safe_path(
            target.repository_path,
            "target.repository_path",
            violations,
            code="ENV_PATH_OUTSIDE_WORKSPACE",
        )
        if not _path_has_prefix(target.repository_path, ("workspace", "repos")):
            _violate(
                violations,
                "ENV_PATH_OUTSIDE_WORKSPACE",
                "target.repository_path",
                "repository_path must be under workspace/repos",
            )


def _check_unique_ids(plan: EnvironmentPlan, violations: list[PolicyViolation]) -> None:
    _check_unique(
        [step.step_id for step in plan.build_steps],
        "build_steps.step_id",
        "duplicate build step id",
        violations,
    )
    _check_unique(
        [step.validation_id for step in plan.validation_steps],
        "validation_steps.validation_id",
        "duplicate validation id",
        violations,
    )
    _check_unique(
        [a.assumption_id for a in plan.assumptions],
        "assumptions.assumption_id",
        "duplicate assumption id",
        violations,
    )


def _check_unique(
    values: list[str],
    location: str,
    message: str,
    violations: list[PolicyViolation],
) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            _violate(violations, "ENV_PLAN_POLICY_DENIED", location, message)
            return
        seen.add(value)


def _check_required_validation(
    steps: list[ValidationStep],
    violations: list[PolicyViolation],
) -> None:
    if not any(step.required for step in steps):
        _violate(
            violations,
            "ENV_PLAN_POLICY_DENIED",
            "validation_steps",
            "at least one required validation is needed",
        )


def _check_assumption(
    assumption: PlanAssumption,
    validation_ids: set[str],
    index: int,
    violations: list[PolicyViolation],
) -> None:
    location = f"assumptions[{index}]"
    if assumption.validation_id is not None and assumption.validation_id not in validation_ids:
        _violate(
            violations,
            "ENV_PLAN_POLICY_DENIED",
            f"{location}.validation_id",
            "assumption validation_id must reference a validation step",
        )
    if assumption.risk == "high" and assumption.validation_id is None:
        _violate(
            violations,
            "ENV_APPROVAL_REQUIRED",
            location,
            "high risk assumption requires validation_id",
        )


def _check_command_step(
    plan: EnvironmentPlan,
    step: CommandStep,
    index: int,
    violations: list[PolicyViolation],
) -> None:
    location = f"build_steps[{index}]"
    program = _program_basename(step.program)
    if program in FORBIDDEN_PROGRAMS:
        _violate(
            violations,
            "ENV_COMMAND_FORBIDDEN",
            f"{location}.program",
            f"forbidden program: {program}",
        )
    if program in SYSTEM_INSTALL_PROGRAMS and not plan.permissions.allow_system_package_install:
        _violate(
            violations,
            "ENV_APPROVAL_REQUIRED",
            f"{location}.program",
            "system package installation requires approval",
        )
    _require_safe_path(step.cwd, f"{location}.cwd", violations)
    if not (
        _path_has_prefix(step.cwd, ("workspace", "repos"))
        or _path_has_prefix(step.cwd, ("runs", plan.run_id))
    ):
        _violate(
            violations,
            "ENV_PATH_OUTSIDE_WORKSPACE",
            f"{location}.cwd",
            f"cwd must be under workspace/repos or runs/{plan.run_id}",
        )
    for arg_index, arg in enumerate(step.args):
        _check_arg(arg, f"{location}.args[{arg_index}]", violations)
    for key in step.environment:
        if key not in ALLOWED_ENVIRONMENT_KEYS:
            _violate(
                violations,
                "ENV_PLAN_POLICY_DENIED",
                f"{location}.environment.{key}",
                f"environment variable is not allowlisted: {key}",
            )
    if step.network and not plan.permissions.network_during_build:
        _violate(
            violations,
            "ENV_PLAN_POLICY_DENIED",
            f"{location}.network",
            "build step network=true but permissions.network_during_build=false",
        )
    if step.modifies_repository and not (
        plan.permissions.allow_repository_modification and step.requires_approval
    ):
        _violate(
            violations,
            "ENV_APPROVAL_REQUIRED",
            f"{location}.modifies_repository",
            "repository modification requires permission and approval",
        )


def _check_validation_step(
    step: ValidationStep,
    index: int,
    violations: list[PolicyViolation],
) -> None:
    location = f"validation_steps[{index}]"
    if step.network is not False:
        _violate(
            violations,
            "ENV_PLAN_POLICY_DENIED",
            f"{location}.network",
            "validation network must be false",
        )


def _check_arg(arg: str, location: str, violations: list[PolicyViolation]) -> None:
    for token in FORBIDDEN_ARG_TOKENS:
        if token in arg:
            _violate(
                violations,
                "ENV_COMMAND_FORBIDDEN",
                location,
                f"shell metacharacter forbidden in arg: {token}",
            )
            return


def _require_safe_path(
    value: str,
    location: str,
    violations: list[PolicyViolation],
    code: str = "ENV_PATH_OUTSIDE_WORKSPACE",
) -> None:
    if "\\" in value:
        _violate(violations, code, location, "backslash forbidden in path")
        return
    path = PurePosixPath(value)
    if path.is_absolute():
        _violate(violations, code, location, "absolute path forbidden")
        return
    if value in {"", "."}:
        _violate(violations, code, location, "path must not be empty or '.'")
        return
    if any(part == ".." for part in path.parts):
        _violate(violations, code, location, "parent traversal forbidden")


def _path_has_prefix(value: str, prefix: tuple[str, ...]) -> bool:
    return PurePosixPath(value).parts[: len(prefix)] == prefix


def _program_basename(value: str) -> str:
    return PurePosixPath(value).name


def _violate(
    violations: list[PolicyViolation],
    code: str,
    location: str,
    message: str,
) -> None:
    violations.append(
        PolicyViolation(code=code, location=location, message=message)
    )
