"""Generic environment build step orchestration."""

import json
from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.environments.adapters import get_environment_adapter
from autoad_researcher.environments.executor import (
    CommandRunner,
    execute_resolved_command,
)
from autoad_researcher.environments.models import EnvironmentPlan
from autoad_researcher.environments.policy import validate_environment_plan_policy
from autoad_researcher.environments.result import EnvironmentBuildResult


def run_environment_build_steps(
    plan: EnvironmentPlan,
    output_dir: Path | str,
    *,
    runner: CommandRunner | None = None,
) -> EnvironmentBuildResult:
    """Run build_steps for a policy-approved EnvironmentPlan.

    Validation and final snapshot collection are intentionally handled by later
    stages. This function persists command evidence and stops at first failure.
    """
    validate_environment_plan_policy(plan)

    build_dir = Path(output_dir)
    logs_dir = build_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    adapter = get_environment_adapter(plan.target.kind)
    commands = adapter.prepare_steps(plan)

    started_at = datetime.now(timezone.utc)
    step_results = []
    failure_code = None
    failure_message = None

    for command in commands:
        result = execute_resolved_command(
            command,
            logs_dir,
            runner=runner,
        )
        step_results.append(result)
        if result.status != "success":
            failure_code = result.failure_code
            failure_message = result.failure_message
            break

    finished_at = datetime.now(timezone.utc)
    status = "failed" if failure_code else "success"
    build_result = EnvironmentBuildResult(
        schema_version=1,
        run_id=plan.run_id,
        plan_id=plan.plan_id,
        plan_sha256=canonical_sha256(plan),
        status=status,
        adapter=adapter.kind,
        environment_path=plan.target.environment_path,
        step_results=step_results,
        snapshot_path=None,
        validation_report_path=None,
        failure_code=failure_code,
        failure_message=failure_message,
        started_at=started_at,
        finished_at=finished_at,
    )

    _write_json(build_dir / "step_results.json", [r.model_dump(mode="json") for r in step_results])
    _write_json(build_dir / "build_result.json", build_result.model_dump(mode="json", exclude_none=True))
    return build_result


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
