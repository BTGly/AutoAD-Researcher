"""PR-001C environment Job wiring and recovery tests."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.assistant.v2.experiment.starter import ExperimentStarter
from autoad_researcher.assistant.v2.job_service import (
    load_pipeline_jobs,
    requeue_stale_running_jobs,
)
from autoad_researcher.assistant.v2.task_bridge import ExperimentTaskDraft
from autoad_researcher.environments.context_collector import CollectedValidationContext
from autoad_researcher.environments.validation import ValidationContext
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.schemas.intake import InputTask
from autoad_researcher.worker.main import _process_pending_jobs


def _confirmed_task(run_id: str) -> ExperimentTaskDraft:
    return ExperimentTaskDraft(
        task_id="task_environment",
        run_id=run_id,
        status="confirmed",
        execution_mode="agent_assisted_after_approval",
        input_task=InputTask(
            run_id=run_id,
            request="prepare environment",
            source_ids=["source_demo"],
            user_idea="environment smoke",
            constraints=[],
        ),
        summary_sha256="a" * 64,
        created_at="2026-07-17T00:00:00+00:00",
        confirmed_at="2026-07-17T00:00:00+00:00",
    )


def test_worker_runs_environment_prepare_to_ready_for_baseline(tmp_path: Path):
    run_dir = tmp_path / "run_environment_worker"
    repository = run_dir / "repos" / "source_demo"
    repository.mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (repository / "README.md").write_text("demo\n", encoding="utf-8")
    task = _confirmed_task(run_dir.name)
    started = ExperimentStarter().on_task_confirmed(
        run_dir,
        task,
        execution_mode="agent_assisted_after_approval",
    )

    assert _process_pending_jobs(run_dir) == 1

    jobs = load_pipeline_jobs(run_dir)
    assert jobs[0]["status"] == "completed"
    session = ExperimentSessionStore().load(run_dir, started.session.session_id)
    assert session is not None
    assert session.status == "READY_FOR_BASELINE"
    assert session.environment_status == "ready"
    assert session.readiness_status == "ready"
    assert session.environment_snapshot_ref == "environment/snapshot.json"
    for relative_path in [
        "environment/host_probe.json",
        "environment/repository_probe.json",
        "environment/plan_r0.json",
        "environment/policy_r0.json",
        "environment/build_r0/build_result.json",
        "environment/validation_context_r0.json",
        "environment/validation_report_r0.json",
        "environment/snapshot.json",
    ]:
        assert (run_dir / relative_path).is_file(), relative_path


def test_worker_blocks_readiness_when_repository_is_ambiguous(tmp_path: Path):
    run_dir = tmp_path / "run_environment_ambiguous"
    for name in ("source_left", "source_right"):
        repository = run_dir / "repos" / name
        repository.mkdir(parents=True)
        (repository / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    task = _confirmed_task(run_dir.name)
    started = ExperimentStarter().on_task_confirmed(
        run_dir,
        task,
        execution_mode="agent_assisted_after_approval",
    )

    assert _process_pending_jobs(run_dir) == 1
    session = ExperimentSessionStore().load(run_dir, started.session.session_id)
    assert session is not None
    assert session.status == "CREATED"
    assert session.readiness_status == "blocked"
    assert "multiple acquired repositories" in session.readiness_blockers[0]
    assert load_pipeline_jobs(run_dir)[0]["status"] == "completed"


def test_stale_running_job_is_requeued_for_worker_recovery(tmp_path: Path):
    run_dir = tmp_path / "run_environment_recovery"
    task = _confirmed_task(run_dir.name)
    started = ExperimentStarter().on_task_confirmed(
        run_dir,
        task,
        execution_mode="agent_assisted_after_approval",
    )
    job_path = run_dir / "jobs" / "pipeline_jobs.jsonl"
    job = load_pipeline_jobs(run_dir)[0]
    job["status"] = "running"
    job["started_at"] = "2026-07-01T00:00:00+00:00"
    job_path.write_text(json.dumps(job) + "\n", encoding="utf-8")

    recovered = requeue_stale_running_jobs(
        run_dir,
        stale_after_seconds=0,
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    assert [item["job_id"] for item in recovered] == [started.environment_job["job_id"]]
    assert load_pipeline_jobs(run_dir)[0]["status"] == "queued"
    assert load_pipeline_jobs(run_dir)[0]["recovery_count"] == 1


def _revision_plan(run_id: str, *, plan_id: str, revision: int, parent_plan_id: str | None, program: str):
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return {
        "schema_version": 1,
        "plan_id": plan_id,
        "run_id": run_id,
        "revision": revision,
        "parent_plan_id": parent_plan_id,
        "target": {
            "kind": "existing_python",
            "environment_path": None,
            "runtime_requirements": {"python": python_version},
            "repository_path": "workspace/repos/source_demo",
        },
        "evidence": [{
            "source_type": "repository",
            "path_or_id": "environment/repository_probe.json",
            "claim": "repository was observed",
        }],
        "build_steps": [{
            "step_id": f"build_r{revision}",
            "program": program,
            "args": [] if program == "false" else ["-c", "import sys"],
            "cwd": "workspace/repos/source_demo",
            "environment": {},
            "timeout_seconds": 30,
            "network": False,
            "modifies_repository": False,
            "requires_approval": False,
        }],
        "validation_steps": [{
            "validation_id": "check_python",
            "kind": "runtime_version",
            "parameters": {"python": python_version},
            "required": True,
            "timeout_seconds": 30,
            "network": False,
        }],
        "permissions": {"max_revision_count": 2},
        "created_by": "user",
    }


def _set_initial_revision_payload(run_dir: Path, plan: dict, revisions: list[dict]) -> None:
    job_path = run_dir / "jobs" / "pipeline_jobs.jsonl"
    job = load_pipeline_jobs(run_dir)[0]
    job["payload"]["environment_plan"] = plan
    job["payload"]["revision_plans"] = revisions
    job_path.write_text(json.dumps(job) + "\n", encoding="utf-8")


def test_failed_revision_schedules_child_job_and_preserves_lineage(tmp_path: Path):
    run_dir = tmp_path / "run_environment_revision"
    repository = run_dir / "repos" / "source_demo"
    repository.mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    task = _confirmed_task(run_dir.name)
    started = ExperimentStarter().on_task_confirmed(
        run_dir, task, execution_mode="agent_assisted_after_approval",
    )
    r0 = _revision_plan(run_dir.name, plan_id="plan_r0", revision=0, parent_plan_id=None, program="false")
    r1 = _revision_plan(run_dir.name, plan_id="plan_r1", revision=1, parent_plan_id="plan_r0", program=sys.executable)
    _set_initial_revision_payload(run_dir, r0, [r1])

    assert _process_pending_jobs(run_dir) == 1
    assert len(load_pipeline_jobs(run_dir)) == 2
    pending_session = ExperimentSessionStore().load(run_dir, started.session.session_id)
    assert pending_session is not None
    assert pending_session.environment_revision == 1
    assert pending_session.status == "ENVIRONMENT_PENDING"
    assert (run_dir / "environment" / "revision_context_r0.json").is_file()

    assert _process_pending_jobs(run_dir) == 1
    session = ExperimentSessionStore().load(run_dir, started.session.session_id)
    assert session is not None
    assert session.status == "READY_FOR_BASELINE"
    assert session.environment_revision == 1
    assert (run_dir / "environment" / "plan_r1.json").is_file()


def test_revision_limit_stops_after_two_children(tmp_path: Path):
    run_dir = tmp_path / "run_environment_revision_limit"
    repository = run_dir / "repos" / "source_demo"
    repository.mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    task = _confirmed_task(run_dir.name)
    started = ExperimentStarter().on_task_confirmed(
        run_dir, task, execution_mode="agent_assisted_after_approval",
    )
    r0 = _revision_plan(run_dir.name, plan_id="limit_r0", revision=0, parent_plan_id=None, program="false")
    r1 = _revision_plan(run_dir.name, plan_id="limit_r1", revision=1, parent_plan_id="limit_r0", program="false")
    r2 = _revision_plan(run_dir.name, plan_id="limit_r2", revision=2, parent_plan_id="limit_r1", program="false")
    _set_initial_revision_payload(run_dir, r0, [r1, r2])

    assert _process_pending_jobs(run_dir) == 1
    assert _process_pending_jobs(run_dir) == 1
    assert _process_pending_jobs(run_dir) == 1

    jobs = load_pipeline_jobs(run_dir)
    assert len(jobs) == 3
    assert jobs[-1]["status"] == "failed"
    session = ExperimentSessionStore().load(run_dir, started.session.session_id)
    assert session is not None
    assert session.status == "ENVIRONMENT_FAILED"
    assert session.environment_revision == 2
    assert (run_dir / "environment" / "revision_context_r2.json").is_file()


def test_gpu_validation_failure_is_explicit_and_never_starts_baseline(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_environment_gpu_failure"
    repository = run_dir / "repos" / "source_demo"
    repository.mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    task = _confirmed_task(run_dir.name)
    started = ExperimentStarter().on_task_confirmed(
        run_dir, task, execution_mode="agent_assisted_after_approval",
    )
    plan = _revision_plan(
        run_dir.name,
        plan_id="gpu_required",
        revision=0,
        parent_plan_id=None,
        program=sys.executable,
    )
    plan["validation_steps"].extend([
        {
            "validation_id": "gpu_available",
            "kind": "gpu_available",
            "parameters": {},
            "required": True,
            "timeout_seconds": 30,
            "network": False,
        },
        {
            "validation_id": "gpu_compute",
            "kind": "gpu_compute",
            "parameters": {},
            "required": True,
            "timeout_seconds": 30,
            "network": False,
        },
    ])
    _set_initial_revision_payload(run_dir, plan, [])
    import autoad_researcher.environments.prepare as prepare_module

    monkeypatch.setattr(
        prepare_module,
        "collect_validation_context",
        lambda *args, **kwargs: CollectedValidationContext(
            python_executable=sys.executable,
            context=ValidationContext(
                runtime_versions={"python": f"{sys.version_info.major}.{sys.version_info.minor}"},
                gpu_available=False,
                gpu_compute_ok=False,
            ),
            package_inventory_sha256="f" * 64,
            command_results=[],
        ),
    )

    assert _process_pending_jobs(run_dir) == 1
    job = load_pipeline_jobs(run_dir)[0]
    session = ExperimentSessionStore().load(run_dir, started.session.session_id)
    assert job["status"] == "failed"
    assert "ENV_GPU_UNAVAILABLE" in job["error"]
    assert session is not None
    assert session.status == "ENVIRONMENT_FAILED"
    assert session.baseline_status == "not_started"
