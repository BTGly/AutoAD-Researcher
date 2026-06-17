"""Tests for bounded EnvironmentPlan revision loop."""

from datetime import datetime, timezone

from autoad_researcher.environments import (
    CommandStepResult,
    EnvironmentAttemptOutcome,
    EnvironmentBuildResult,
    EnvironmentPlan,
    ValidationContext,
    build_revision_context,
    run_bounded_revision_loop,
    validate_environment,
)
from tests.test_environment_plan_models import valid_plan


def make_plan(*, revision: int = 0, parent_plan_id: str | None = None) -> EnvironmentPlan:
    data = valid_plan(
        plan_id=f"plan_cpu_uv_v{revision}",
        revision=revision,
        parent_plan_id=parent_plan_id,
    )
    return EnvironmentPlan.model_validate(data)


def failed_outcome(plan: EnvironmentPlan) -> EnvironmentAttemptOutcome:
    return EnvironmentAttemptOutcome(
        plan_id=plan.plan_id,
        revision=plan.revision,
        status="failed",
        failure_code="ENV_INSTALL_FAILED",
        failure_message="install failed",
    )


def test_revision_loop_succeeds_after_one_revision():
    calls = []

    def runner(plan: EnvironmentPlan) -> EnvironmentAttemptOutcome:
        calls.append(plan.plan_id)
        if plan.revision == 0:
            return failed_outcome(plan)
        return EnvironmentAttemptOutcome(
            plan_id=plan.plan_id,
            revision=plan.revision,
            status="success",
        )

    def provider(plan: EnvironmentPlan, outcome: EnvironmentAttemptOutcome) -> EnvironmentPlan:
        return make_plan(revision=1, parent_plan_id=plan.plan_id)

    result = run_bounded_revision_loop(make_plan(), runner, provider)

    assert result.status == "success"
    assert calls == ["plan_cpu_uv_v0", "plan_cpu_uv_v1"]
    assert result.final_plan_id == "plan_cpu_uv_v1"


def test_revision_loop_stops_at_limit():
    def provider(plan: EnvironmentPlan, outcome: EnvironmentAttemptOutcome) -> EnvironmentPlan:
        return make_plan(revision=plan.revision + 1, parent_plan_id=plan.plan_id)

    result = run_bounded_revision_loop(make_plan(), failed_outcome, provider)

    assert result.status == "revision_limit_exceeded"
    assert [a.revision for a in result.attempts] == [0, 1, 2]


def test_revision_loop_stops_when_provider_returns_none():
    result = run_bounded_revision_loop(make_plan(), failed_outcome, lambda *_: None)

    assert result.status == "failed"
    assert len(result.attempts) == 1


def test_revision_loop_rejects_bad_parent():
    def provider(plan: EnvironmentPlan, outcome: EnvironmentAttemptOutcome) -> EnvironmentPlan:
        return make_plan(revision=1, parent_plan_id="wrong_parent")

    try:
        run_bounded_revision_loop(make_plan(), failed_outcome, provider)
    except ValueError as exc:
        assert "parent_plan_id" in str(exc)
    else:
        raise AssertionError("bad parent was not rejected")


def test_build_revision_context_from_build_and_validation_failures():
    plan = make_plan()
    now = datetime.now(timezone.utc)
    step_result = CommandStepResult(
        schema_version=1,
        step_id="install_project",
        command_sha256="a" * 64,
        status="failed",
        exit_code=1,
        stdout_path="install.stdout.log",
        stderr_path="install.stderr.log",
        failure_code="ENV_COMMAND_FAILED",
        failure_message="command exited with code 1",
        started_at=now,
        finished_at=now,
        duration_seconds=0,
    )
    build_result = EnvironmentBuildResult(
        schema_version=1,
        run_id=plan.run_id,
        plan_id=plan.plan_id,
        plan_sha256="b" * 64,
        status="failed",
        adapter=plan.target.kind,
        environment_path=plan.target.environment_path,
        step_results=[step_result],
        failure_code="ENV_INSTALL_FAILED",
        failure_message="install failed",
        started_at=now,
        finished_at=now,
    )
    validation_report = validate_environment(
        plan,
        ValidationContext(runtime_versions={"python": "3.10"}),
    )

    context = build_revision_context(
        plan,
        build_result=build_result,
        validation_report=validation_report,
    )

    assert context.failed_step == "install_project"
    assert context.failure_code == "ENV_INSTALL_FAILED"
    assert context.failed_validations == ["check_python"]
    assert "environment/build_result.json" in context.suggested_evidence
