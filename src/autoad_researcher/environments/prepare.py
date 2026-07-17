"""Worker-owned environment preparation transaction for one Session revision."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.environments.builder import run_environment_build_steps
from autoad_researcher.environments.context_collector import collect_validation_context
from autoad_researcher.environments.executor import CommandExecutionOutput
from autoad_researcher.environments.models import (
    CommandStep,
    EnvironmentPermissions,
    EnvironmentPlan,
    EnvironmentTarget,
    EvidenceReference,
    ValidationStep,
)
from autoad_researcher.environments.policy import evaluate_environment_plan_policy
from autoad_researcher.environments.probe import (
    RepositoryProbe,
    probe_host,
    probe_repository,
    write_probe,
)
from autoad_researcher.environments.result import ResolvedCommand
from autoad_researcher.environments.revision import build_revision_context
from autoad_researcher.environments.snapshot import build_observed_environment_snapshot
from autoad_researcher.environments.validation import validate_environment
from autoad_researcher.experiment.session_store import ExperimentSessionStore


class EnvironmentPreparationError(RuntimeError):
    """A durable environment failure whose message is safe for the PipelineJob."""


def prepare_environment_for_job(run_dir: Path, job: dict[str, Any]) -> list[str]:
    """Run probe → plan → policy → build → context → validate for one Job."""
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    session_id = _required_string(payload, "session_id")
    revision = _required_revision(payload)
    store = ExperimentSessionStore()
    session = store.load(run_dir, session_id)
    if session is None:
        raise EnvironmentPreparationError("experiment session not found")
    if session.environment_revision != revision:
        raise EnvironmentPreparationError("environment job revision does not match Session")

    environment_dir = run_dir / "environment"
    plan: EnvironmentPlan | None = None
    build = None
    report = None
    try:
        store.update_environment_state(
            run_dir,
            session_id=session_id,
            status="ENVIRONMENT_PENDING",
            environment_status="pending",
            readiness_status="resolving",
            readiness_blockers=[],
        )
        host = probe_host(environment_dir)
        write_probe(environment_dir / "host_probe.json", host)
        repositories = _candidate_repositories(run_dir)
        if len(repositories) != 1:
            blockers = (
                ["repository target is unresolved: no acquired repository was found"]
                if not repositories
                else ["repository target is unresolved: multiple acquired repositories were found"]
            )
            _write_json(environment_dir / "readiness_blocked.json", {"blockers": blockers})
            store.update_environment_state(
                run_dir,
                session_id=session_id,
                status="CREATED",
                environment_status="not_started",
                readiness_status="blocked",
                readiness_blockers=blockers,
            )
            return _relative_outputs(run_dir, environment_dir / "host_probe.json", environment_dir / "readiness_blocked.json")

        source_id, repository_path = repositories[0]
        repository = probe_repository(repository_path, environment_dir, source_id=source_id)
        write_probe(environment_dir / "repository_probe.json", repository)
        store.update_environment_state(
            run_dir,
            session_id=session_id,
            status="ENVIRONMENT_RUNNING",
            environment_status="running",
            readiness_status="resolving",
            repository_ref=f"repos/{source_id}",
        )
        plan = _load_or_build_plan(
            payload,
            run_id=run_dir.name,
            session_id=session_id,
            revision=revision,
            source_id=source_id,
            host=host,
            environment_dir=environment_dir,
        )
        plan_path = environment_dir / f"plan_r{revision}.json"
        _write_json(plan_path, plan.model_dump(mode="json"))
        policy = evaluate_environment_plan_policy(plan)
        policy_path = environment_dir / f"policy_r{revision}.json"
        _write_json(policy_path, policy.model_dump(mode="json"))
        if policy.status != "passed":
            raise EnvironmentPreparationError("environment plan policy denied")

        build_dir = environment_dir / f"build_r{revision}"
        build = run_environment_build_steps(
            plan,
            build_dir,
            runner=_workspace_build_runner(run_dir, build_dir, revision),
        )
        if build.status != "success":
            raise EnvironmentPreparationError(build.failure_message or "environment build failed")
        python_executable = _prepared_python_executable(run_dir, build_dir, revision, plan)
        context = collect_validation_context(
            plan,
            python_executable=python_executable,
            repository_probe=repository,
            output_dir=environment_dir / f"validation_context_r{revision}",
        )
        context_path = environment_dir / f"validation_context_r{revision}.json"
        _write_json(context_path, context.model_dump(mode="json"))
        report = validate_environment(plan, context.context)
        report_path = environment_dir / f"validation_report_r{revision}.json"
        _write_json(report_path, report.model_dump(mode="json"))
        if report.status != "passed":
            failed_codes = [
                result.code for result in report.results
                if result.status == "failed"
            ]
            detail = failed_codes[0] if failed_codes else "ENV_VALIDATION_FAILED"
            raise EnvironmentPreparationError(f"environment validation failed: {detail}")
        snapshot = build_observed_environment_snapshot(plan, build, context, report)
        snapshot_path = environment_dir / "snapshot.json"
        _write_json(snapshot_path, snapshot.model_dump(mode="json", exclude_none=True))
        store.update_environment_state(
            run_dir,
            session_id=session_id,
            status="READY_FOR_BASELINE",
            environment_status="ready",
            readiness_status="ready",
            readiness_blockers=[],
            environment_snapshot_ref="environment/snapshot.json",
        )
        return _relative_outputs(
            run_dir,
            environment_dir / "host_probe.json",
            environment_dir / "repository_probe.json",
            plan_path,
            policy_path,
            build_dir / "build_result.json",
            context_path,
            report_path,
            snapshot_path,
        )
    except EnvironmentPreparationError as exc:
        revision_outputs = _schedule_revision_if_available(
            run_dir,
            payload=payload,
            session_id=session_id,
            revision=revision,
            plan=plan,
            build_result=build,
            validation_report=report,
            environment_dir=environment_dir,
        )
        if revision_outputs is not None:
            return revision_outputs
        store.update_environment_state(
            run_dir,
            session_id=session_id,
            status="ENVIRONMENT_FAILED",
            environment_status="failed",
            readiness_status="blocked",
            readiness_blockers=["environment preparation failed; inspect environment artifacts"],
        )
        raise exc
    except Exception as exc:
        store.update_environment_state(
            run_dir,
            session_id=session_id,
            status="ENVIRONMENT_FAILED",
            environment_status="failed",
            readiness_status="blocked",
            readiness_blockers=["environment preparation failed; inspect environment artifacts"],
        )
        raise EnvironmentPreparationError(str(exc)) from exc


def _schedule_revision_if_available(
    run_dir: Path,
    *,
    payload: dict[str, Any],
    session_id: str,
    revision: int,
    plan: EnvironmentPlan | None,
    build_result,
    validation_report,
    environment_dir: Path,
) -> list[str] | None:
    if plan is None:
        return None
    context = build_revision_context(
        plan,
        build_result=build_result,
        validation_report=validation_report,
    )
    context_path = environment_dir / f"revision_context_r{revision}.json"
    _write_json(context_path, context.model_dump(mode="json"))
    candidates = payload.get("revision_plans")
    if not isinstance(candidates, list) or revision >= plan.permissions.max_revision_count:
        return None
    next_plan = next(
        (
            EnvironmentPlan.model_validate(candidate)
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("revision") == revision + 1
        ),
        None,
    )
    if next_plan is None:
        return None
    if next_plan.parent_plan_id != plan.plan_id or next_plan.run_id != plan.run_id:
        raise EnvironmentPreparationError("revised plan does not continue failed plan lineage")
    session = ExperimentSessionStore().advance_environment_revision(
        run_dir,
        session_id=session_id,
        expected_revision=revision,
    )
    from autoad_researcher.assistant.v2.event_service import append_event
    from autoad_researcher.assistant.v2.job_service import create_or_get_pipeline_job

    remaining = [
        candidate for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("revision", -1) > next_plan.revision
    ]
    next_job, created = create_or_get_pipeline_job(
        run_dir,
        source_id=session_id,
        job_type="experiment_environment_prepare",
        idempotency_key=f"environment_prepare:{session_id}:r{next_plan.revision}",
        evidence_role="experiment_environment_prepare",
        payload={
            "session_id": session_id,
            "task_ref": str(payload.get("task_ref") or "input_task.yaml"),
            "environment_revision": next_plan.revision,
            "environment_plan": next_plan.model_dump(mode="json"),
            "revision_plans": remaining,
        },
    )
    if created:
        append_event(
            run_dir,
            "experiment.environment_prepare.revision_queued",
            {
                "session_id": session_id,
                "parent_revision": revision,
                "revision": next_plan.revision,
                "job_id": next_job["job_id"],
            },
        )
    return _relative_outputs(run_dir, context_path)


def _load_or_build_plan(
    payload: dict[str, Any],
    *,
    run_id: str,
    session_id: str,
    revision: int,
    source_id: str,
    host,
    environment_dir: Path,
) -> EnvironmentPlan:
    supplied = payload.get("environment_plan")
    if isinstance(supplied, dict):
        plan = EnvironmentPlan.model_validate(supplied)
        if plan.run_id != run_id or plan.revision != revision:
            raise EnvironmentPreparationError("supplied environment plan does not match Job identity")
        return plan
    python_version = str(host.operating_system.get("python") or "")
    if not python_version:
        raise EnvironmentPreparationError("host Python version probe failed")
    return EnvironmentPlan(
        schema_version=1,
        plan_id=f"environment_{session_id}_r{revision}",
        run_id=run_id,
        revision=revision,
        parent_plan_id=None if revision == 0 else f"environment_{session_id}_r{revision - 1}",
        target=EnvironmentTarget(
            kind="existing_python",
            environment_path=None,
            runtime_requirements={"python": python_version},
            repository_path=f"workspace/repos/{source_id}",
        ),
        evidence=[
            EvidenceReference(
                source_type="host",
                path_or_id="environment/host_probe.json",
                claim="Host Python was observed by the environment probe.",
                sha256=sha256_file(environment_dir / "host_probe.json"),
            ),
            EvidenceReference(
                source_type="repository",
                path_or_id="environment/repository_probe.json",
                claim="Repository structure was observed by the environment probe.",
                sha256=sha256_file(environment_dir / "repository_probe.json"),
            ),
        ],
        build_steps=[
            CommandStep(
                step_id="verify_existing_python",
                program=sys.executable,
                args=["-c", "import sys"],
                cwd=f"workspace/repos/{source_id}",
                environment={},
                timeout_seconds=30,
                network=False,
                modifies_repository=False,
                requires_approval=False,
            )
        ],
        validation_steps=[
            ValidationStep(
                validation_id="check_python",
                kind="runtime_version",
                parameters={"python": python_version},
                required=True,
                timeout_seconds=30,
                network=False,
            ),
            ValidationStep(
                validation_id="repository_clean",
                kind="repository_clean",
                parameters={},
                required=True,
                timeout_seconds=30,
                network=False,
            ),
        ],
        permissions=EnvironmentPermissions(max_revision_count=2),
        created_by="user",
    )


def _workspace_build_runner(run_dir: Path, build_dir: Path, revision: int):
    def runner(command: ResolvedCommand) -> CommandExecutionOutput:
        translated = command.model_copy(
            update={
                "cwd": _resolve_workspace_path(run_dir, build_dir, revision, command.cwd),
                "args": [
                    _resolve_workspace_path(run_dir, build_dir, revision, argument)
                    for argument in command.args
                ],
            }
        )
        try:
            completed = subprocess.run(
                translated.argv,
                cwd=translated.cwd,
                env={**os.environ, **translated.environment},
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=translated.timeout_seconds,
            )
            return CommandExecutionOutput(
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandExecutionOutput(
                exit_code=None,
                stdout=exc.stdout if isinstance(exc.stdout, str) else "",
                stderr=exc.stderr if isinstance(exc.stderr, str) else "",
                timed_out=True,
            )
    return runner


def _prepared_python_executable(
    run_dir: Path,
    build_dir: Path,
    revision: int,
    plan: EnvironmentPlan,
) -> str:
    if plan.target.kind == "existing_python":
        return sys.executable
    if plan.target.environment_path is None:
        raise EnvironmentPreparationError("environment plan has no environment path")
    return str(Path(_resolve_workspace_path(run_dir, build_dir, revision, plan.target.environment_path)) / "bin" / "python")


def _resolve_workspace_path(run_dir: Path, build_dir: Path, revision: int, value: str) -> str:
    del revision
    if value.startswith("workspace/repos/"):
        return str(run_dir / "repos" / value.removeprefix("workspace/repos/"))
    if value.startswith("workspace/envs/"):
        return str(build_dir / "envs" / value.removeprefix("workspace/envs/"))
    if value.startswith(f"runs/{run_dir.name}/"):
        return str(run_dir / value.removeprefix(f"runs/{run_dir.name}/"))
    return value


def _candidate_repositories(run_dir: Path) -> list[tuple[str, Path]]:
    root = run_dir / "repos"
    if not root.is_dir():
        return []
    return [
        (child.name, child)
        for child in sorted(root.iterdir())
        if child.is_dir() and not child.is_symlink()
    ]


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise EnvironmentPreparationError(f"environment Job payload missing {key}")
    return value


def _required_revision(payload: dict[str, Any]) -> int:
    value = payload.get("environment_revision")
    if not isinstance(value, int) or value < 0:
        raise EnvironmentPreparationError("environment Job payload has invalid environment_revision")
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _relative_outputs(run_dir: Path, *paths: Path) -> list[str]:
    return [path.relative_to(run_dir).as_posix() for path in paths if path.is_file()]
