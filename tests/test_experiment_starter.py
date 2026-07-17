"""PR-001A reconciliation and experiment-control-plane tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.event_service import load_events_since
from autoad_researcher.assistant.v2.experiment.starter import ExperimentStarter
from autoad_researcher.assistant.v2.job_service import (
    create_or_get_pipeline_job,
    load_pipeline_jobs,
)
from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    save_research_intent_summary,
)
from autoad_researcher.assistant.v2.task_bridge import TaskBridge
from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.server.routes import runs as runs_route


def _draft(run_dir: Path):
    save_research_intent_summary(
        run_dir,
        ResearchIntentSummary(goal="比较候选方法", blocking_question=None),
    )
    return TaskBridge.build_experiment_task(run_dir, user_input="准备实验")


def _confirmed(run_dir: Path):
    draft = _draft(run_dir)
    return TaskBridge.confirm_or_load_existing(
        run_dir,
        task_id=draft.task_id,
        execution_mode="agent_assisted_after_approval",
    )


def test_replayed_confirm_creates_one_session_and_one_environment_job(tmp_path: Path):
    run_dir = tmp_path / "run_replay"
    run_dir.mkdir()
    task = _confirmed(run_dir)

    first = ExperimentStarter().on_task_confirmed(
        run_dir,
        task,
        execution_mode="agent_assisted_after_approval",
    )
    replayed_task = TaskBridge.confirm_or_load_existing(
        run_dir,
        task_id=task.task_id,
        execution_mode="agent_assisted_after_approval",
    )
    second = ExperimentStarter().on_task_confirmed(
        run_dir,
        replayed_task,
        execution_mode="agent_assisted_after_approval",
    )

    assert first.disposition == "created"
    assert second.disposition == "reused"
    assert first.session.session_id == second.session.session_id
    assert first.environment_job["job_id"] == second.environment_job["job_id"]
    assert len(load_pipeline_jobs(run_dir)) == 1
    event_types = [event["type"] for event in load_events_since(run_dir)]
    assert "experiment.start_requested" in event_types
    assert "experiment.session.created" in event_types
    assert "experiment.environment_prepare.queued" in event_types


def test_confirm_rebuilds_yaml_then_starter_recovers_session_gap(tmp_path: Path):
    run_dir = tmp_path / "run_yaml_only"
    run_dir.mkdir()
    task = _confirmed(run_dir)
    (run_dir / "input_task.yaml").unlink()

    replayed = TaskBridge.confirm_or_load_existing(
        run_dir,
        task_id=task.task_id,
        execution_mode="agent_assisted_after_approval",
    )
    result = ExperimentStarter().on_task_confirmed(
        run_dir,
        replayed,
        execution_mode="agent_assisted_after_approval",
    )

    assert (run_dir / "input_task.yaml").is_file()
    assert result.disposition == "created"
    assert len(load_pipeline_jobs(run_dir)) == 1


def test_starter_repairs_missing_job_after_session_creation(tmp_path: Path):
    run_dir = tmp_path / "run_session_only"
    run_dir.mkdir()
    task = _confirmed(run_dir)
    task_hash = canonical_sha256(task.input_task)
    session, created = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash=task_hash,
        execution_mode="agent_assisted_after_approval",
    )

    result = ExperimentStarter().on_task_confirmed(
        run_dir,
        task,
        execution_mode="agent_assisted_after_approval",
    )

    assert created is True
    assert result.disposition == "repaired"
    assert result.session.session_id == session.session_id
    assert len(load_pipeline_jobs(run_dir)) == 1


def test_same_job_idempotency_key_rejects_different_payload(tmp_path: Path):
    run_dir = tmp_path / "run_job_conflict"
    run_dir.mkdir()
    job, created = create_or_get_pipeline_job(
        run_dir,
        source_id="session_a",
        job_type="experiment_environment_prepare",
        idempotency_key="environment_prepare:session_a:r0",
        evidence_role="experiment_environment_prepare",
        payload={"session_id": "session_a", "environment_revision": 0},
    )
    assert created is True
    assert job["job_id"] == "job_000001"

    with pytest.raises(ValueError, match="different job identity"):
        create_or_get_pipeline_job(
            run_dir,
            source_id="session_a",
            job_type="experiment_environment_prepare",
            idempotency_key="environment_prepare:session_a:r0",
            evidence_role="experiment_environment_prepare",
            payload={"session_id": "session_a", "environment_revision": 1},
        )


def test_authorization_change_is_revisioned_without_new_session_or_job(tmp_path: Path):
    run_dir = tmp_path / "run_authorization"
    run_dir.mkdir()
    task = _confirmed(run_dir)
    starter = ExperimentStarter()
    first = starter.on_task_confirmed(
        run_dir,
        task,
        execution_mode="agent_assisted_after_approval",
    )
    second = starter.on_task_confirmed(
        run_dir,
        task,
        execution_mode="approve_each_step",
    )

    assert second.disposition == "reused"
    assert second.session.session_id == first.session.session_id
    assert second.session.authorization.execution_mode == "approve_each_step"
    assert second.session.authorization_revision == 1
    assert len(load_pipeline_jobs(run_dir)) == 1
    assert "experiment.authorization.changed" in {
        event["type"] for event in load_events_since(run_dir)
    }


def test_plan_only_confirmation_never_creates_session_or_job(tmp_path: Path):
    run_dir = tmp_path / "run_plan_only"
    run_dir.mkdir()
    draft = _draft(run_dir)
    confirmed = TaskBridge.confirm_or_load_existing(
        run_dir,
        task_id=draft.task_id,
        execution_mode="plan_only",
    )

    assert confirmed.execution_mode == "plan_only"
    assert not (run_dir / "experiments" / "sessions").exists()
    assert load_pipeline_jobs(run_dir) == []


def test_existing_confirmed_files_must_match_authoritative_draft(tmp_path: Path):
    run_dir = tmp_path / "run_conflict"
    run_dir.mkdir()
    task = _confirmed(run_dir)
    (run_dir / "input_task.yaml").write_text("run_id: different\n", encoding="utf-8")

    with pytest.raises(ValueError, match="existing input_task.yaml"):
        TaskBridge.confirm_or_load_existing(
            run_dir,
            task_id=task.task_id,
            execution_mode="agent_assisted_after_approval",
        )


@pytest.mark.asyncio
async def test_confirm_route_returns_control_plane_references(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_confirm_api"
    run_dir.mkdir()
    draft = _draft(run_dir)
    monkeypatch.setattr(runs_route, "RUNS_ROOT", str(tmp_path))

    result = await runs_route.confirm_experiment_task(
        run_dir.name,
        draft.task_id,
        runs_route.ConfirmExperimentTaskRequest(
            execution_mode="agent_assisted_after_approval",
        ),
    )

    assert result.disposition == "created"
    assert result.session_id is not None
    assert result.environment_job_id == "job_000001"
