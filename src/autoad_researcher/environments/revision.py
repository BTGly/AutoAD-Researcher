"""Bounded EnvironmentPlan revision loop."""

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.environments.models import EnvironmentPlan
from autoad_researcher.environments.result import EnvironmentBuildResult
from autoad_researcher.environments.validation import ValidationReport


class EnvironmentRevisionContext(BaseModel):
    """LLM-consumable context for revising a failed EnvironmentPlan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    original_plan_id: str
    failed_step: str | None
    failure_code: str
    failure_message: str
    failed_validations: list[str] = Field(default_factory=list)
    suggested_evidence: list[str] = Field(default_factory=list)


class EnvironmentAttemptOutcome(BaseModel):
    """Minimal outcome consumed by the bounded revision loop."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    plan_id: str
    revision: int
    status: Literal["success", "failed"]
    failure_code: str | None = None
    failure_message: str | None = None


class EnvironmentRevisionLoopResult(BaseModel):
    """Final result of a bounded revision loop."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    run_id: str
    status: Literal["success", "failed", "revision_limit_exceeded"]
    attempts: list[EnvironmentAttemptOutcome]
    final_plan_id: str
    failure_code: str | None = None
    failure_message: str | None = None


AttemptRunner = Callable[[EnvironmentPlan], EnvironmentAttemptOutcome]
RevisionProvider = Callable[[EnvironmentPlan, EnvironmentAttemptOutcome], EnvironmentPlan | None]


def build_revision_context(
    plan: EnvironmentPlan,
    *,
    build_result: EnvironmentBuildResult | None = None,
    validation_report: ValidationReport | None = None,
) -> EnvironmentRevisionContext:
    """Create structured context from build and validation failures."""
    failed_step = None
    failure_code = "ENV_PLAN_FAILED"
    failure_message = "environment plan failed"
    suggested_evidence: list[str] = []
    failed_validations: list[str] = []

    if build_result and build_result.status == "failed":
        failed_step_result = next(
            (step for step in build_result.step_results if step.status != "success"),
            None,
        )
        failed_step = failed_step_result.step_id if failed_step_result else None
        failure_code = build_result.failure_code or failure_code
        failure_message = build_result.failure_message or failure_message
        suggested_evidence.extend(["environment/build_result.json", "environment/step_results.json"])

    if validation_report and validation_report.status == "failed":
        failed_validations = [
            result.validation_id
            for result in validation_report.results
            if result.status == "failed"
        ]
        if not build_result or build_result.status != "failed":
            failure_code = "ENV_VALIDATION_FAILED"
            failure_message = "environment validation failed"
        suggested_evidence.append("environment/validation_report.json")

    return EnvironmentRevisionContext(
        original_plan_id=plan.plan_id,
        failed_step=failed_step,
        failure_code=failure_code,
        failure_message=failure_message,
        failed_validations=failed_validations,
        suggested_evidence=suggested_evidence,
    )


def run_bounded_revision_loop(
    initial_plan: EnvironmentPlan,
    attempt_runner: AttemptRunner,
    revision_provider: RevisionProvider,
) -> EnvironmentRevisionLoopResult:
    """Run plan attempts until success or revision limit is reached."""
    current = initial_plan
    attempts: list[EnvironmentAttemptOutcome] = []
    max_revisions = initial_plan.permissions.max_revision_count

    while True:
        outcome = attempt_runner(current)
        _validate_outcome_matches_plan(current, outcome)
        attempts.append(outcome)

        if outcome.status == "success":
            return EnvironmentRevisionLoopResult(
                schema_version=1,
                run_id=initial_plan.run_id,
                status="success",
                attempts=attempts,
                final_plan_id=current.plan_id,
            )

        if current.revision >= max_revisions:
            return EnvironmentRevisionLoopResult(
                schema_version=1,
                run_id=initial_plan.run_id,
                status="revision_limit_exceeded",
                attempts=attempts,
                final_plan_id=current.plan_id,
                failure_code=outcome.failure_code or "ENV_REVISION_LIMIT_EXCEEDED",
                failure_message=outcome.failure_message or "revision limit exceeded",
            )

        replacement = revision_provider(current, outcome)
        if replacement is None:
            return EnvironmentRevisionLoopResult(
                schema_version=1,
                run_id=initial_plan.run_id,
                status="failed",
                attempts=attempts,
                final_plan_id=current.plan_id,
                failure_code=outcome.failure_code,
                failure_message=outcome.failure_message,
            )
        _validate_replacement_plan(current, replacement, max_revisions)
        current = replacement


def _validate_outcome_matches_plan(plan: EnvironmentPlan, outcome: EnvironmentAttemptOutcome) -> None:
    if outcome.plan_id != plan.plan_id or outcome.revision != plan.revision:
        raise ValueError("attempt outcome does not match attempted plan")
    if outcome.status == "failed" and (not outcome.failure_code or not outcome.failure_message):
        raise ValueError("failed attempt outcome requires failure fields")


def _validate_replacement_plan(
    current: EnvironmentPlan,
    replacement: EnvironmentPlan,
    max_revisions: int,
) -> None:
    expected_revision = current.revision + 1
    if replacement.parent_plan_id != current.plan_id:
        raise ValueError("replacement plan parent_plan_id mismatch")
    if replacement.revision != expected_revision:
        raise ValueError("replacement plan revision must increment by one")
    if replacement.revision > max_revisions:
        raise ValueError("replacement plan exceeds max_revision_count")
    if replacement.run_id != current.run_id:
        raise ValueError("replacement plan run_id mismatch")
