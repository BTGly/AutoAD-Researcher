"""PR-001C environment Job wiring and recovery tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.assistant.v2.experiment.starter import ExperimentStarter
from autoad_researcher.assistant.v2.job_service import (
    load_pipeline_jobs,
    requeue_stale_running_jobs,
)
from autoad_researcher.assistant.v2.task_bridge import ExperimentTaskDraft
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
    for relative_path in [
        "environment/host_probe.json",
        "environment/repository_probe.json",
        "environment/plan_r0.json",
        "environment/policy_r0.json",
        "environment/build_r0/build_result.json",
        "environment/validation_context_r0.json",
        "environment/validation_report_r0.json",
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
    job_path.write_text(__import__("json").dumps(job) + "\n", encoding="utf-8")

    recovered = requeue_stale_running_jobs(
        run_dir,
        stale_after_seconds=0,
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    assert [item["job_id"] for item in recovered] == [started.environment_job["job_id"]]
    assert load_pipeline_jobs(run_dir)[0]["status"] == "queued"
    assert load_pipeline_jobs(run_dir)[0]["recovery_count"] == 1
