"""PR-001A reconciliation and experiment-control-plane tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from autoad_researcher.assistant.v2.event_service import load_events_since
from autoad_researcher.assistant.v2.experiment.starter import ExperimentStarter
from autoad_researcher.assistant.v2.job_service import (
    create_or_get_pipeline_job,
    load_pipeline_jobs,
)
from autoad_researcher.assistant.v2.research_intent_summary import (
    ConfirmedTaskParameters,
    ResearchIntentSummary,
    save_research_intent_summary,
)
from autoad_researcher.assistant.v2.task_bridge import TaskBridge, TaskConfirmationConflict
from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.repository_intelligence.acquisition import RepositoryAttestation
from autoad_researcher.schemas.decisions import ConfirmedDecision
from autoad_researcher.server.routes import runs as runs_route
from autoad_researcher.ui.sources import append_source_ref


EXECUTION_SOURCE_ID = "src_execution"


def _prepare_executable_source(run_dir: Path) -> None:
    append_source_ref(
        run_dir,
        source_id=EXECUTION_SOURCE_ID,
        kind="local_repo",
        user_label="candidate repository",
        stored_path=f"repos/{EXECUTION_SOURCE_ID}",
        status="parsed",
        intake_status="ok",
    )
    repository = run_dir / "repos" / EXECUTION_SOURCE_ID
    repository.mkdir(parents=True)
    (repository / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (repository / "evaluation.py").write_text("", encoding="utf-8")
    (repository / "autoad_executor_adapter.json").write_text(json.dumps({
        "adapter_id": "generic_python",
        "entrypoint": "run.py",
        "smoke_argv": [sys.executable, "run.py"],
        "metrics_output": "metrics.json",
        "allowed_paths": ["run.py"],
        "protected_paths": ["evaluation.py"],
        "activation_evidence": "observed",
    }), encoding="utf-8")
    attestation = RepositoryAttestation(
        schema_version=1,
        source_id=EXECUTION_SOURCE_ID,
        repository_root_label=f"local/{EXECUTION_SOURCE_ID}",
        canonical_remote_url=None,
        head_commit=None,
        git_tree_sha=None,
        tree_sha="b" * 64,
        detached_head=None,
        dirty=False,
        git_status_porcelain="",
        symbolic_ref=None,
        submodule_declarations=[],
        tool_call_ids=["tool_local_tree_fingerprint"],
    )
    path = run_dir / "repo_acquisition" / EXECUTION_SOURCE_ID / "repository_attestation.json"
    path.parent.mkdir(parents=True)
    path.write_text(attestation.model_dump_json(), encoding="utf-8")


def _draft(run_dir: Path):
    _prepare_executable_source(run_dir)
    save_research_intent_summary(
        run_dir,
        ResearchIntentSummary(
            goal="比较候选方法",
            blocking_question=None,
            confirmed_task_parameters=ConfirmedTaskParameters(
                primary_metrics=[
                    ConfirmedDecision(
                        value="image_auroc",
                        source="user_confirmed",
                        evidence="test fixture metric confirmation",
                    )
                ]
            ),
        ),
    )
    return TaskBridge.build_experiment_task(run_dir, user_input="准备实验")


def _confirmed(run_dir: Path):
    draft = _draft(run_dir)
    return TaskBridge.confirm_or_load_existing(
        run_dir,
        task_id=draft.task_id,
        execution_mode="agent_assisted_after_approval",
        execution_repository_source_id=EXECUTION_SOURCE_ID,
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
    task_hash = canonical_sha256({
        "input_task": task.input_task.model_dump(mode="json"),
        "execution_repository_binding": task.execution_repository_binding.model_dump(mode="json"),
    })
    session, created = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash=task_hash,
        execution_mode="agent_assisted_after_approval",
        repository_ref=task.execution_repository_binding.repository_ref,
        execution_repository_binding_ref="task_bridge/execution_repository_binding.json",
        execution_repository_binding_sha256=canonical_sha256(task.execution_repository_binding),
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


def test_pending_task_can_be_loaded_for_browser_reload(tmp_path: Path):
    run_dir = tmp_path / "run_pending_browser_reload"
    run_dir.mkdir()
    draft = _draft(run_dir)

    loaded = TaskBridge.load_pending_experiment_task(run_dir)

    assert loaded == draft


def test_pending_task_route_returns_the_durable_draft(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_pending_route"
    run_dir.mkdir()
    draft = _draft(run_dir)
    monkeypatch.setattr(runs_route, "RUNS_ROOT", tmp_path)

    import asyncio
    loaded = asyncio.run(runs_route.get_pending_experiment_task(run_dir.name))

    assert loaded == draft


def test_execution_confirmation_without_source_selection_has_zero_execution_side_effects(tmp_path: Path):
    run_dir = tmp_path / "run_missing_execution_source"
    run_dir.mkdir()
    draft = _draft(run_dir)

    with pytest.raises(TaskConfirmationConflict) as excinfo:
        TaskBridge.confirm_or_load_existing(
            run_dir,
            task_id=draft.task_id,
            execution_mode="agent_assisted_after_approval",
        )

    assert excinfo.value.code == "execution_repository_unresolved"
    assert not (run_dir / "input_task.yaml").exists()
    assert not (run_dir / "task_bridge" / "execution_repository_binding.json").exists()
    assert not (run_dir / "experiments" / "sessions").exists()
    assert load_pipeline_jobs(run_dir) == []


def test_execution_confirmation_without_primary_metric_has_zero_execution_side_effects(tmp_path: Path):
    run_dir = tmp_path / "run_missing_primary_metric"
    run_dir.mkdir()
    _prepare_executable_source(run_dir)
    save_research_intent_summary(run_dir, ResearchIntentSummary(goal="比较候选方法"))
    draft = TaskBridge.build_experiment_task(run_dir, user_input="准备实验")

    with pytest.raises(TaskConfirmationConflict) as excinfo:
        TaskBridge.confirm_or_load_existing(
            run_dir,
            task_id=draft.task_id,
            execution_mode="agent_assisted_after_approval",
            execution_repository_source_id=EXECUTION_SOURCE_ID,
        )

    assert excinfo.value.code == "execution_contract_incomplete"
    assert not (run_dir / "input_task.yaml").exists()
    assert not (run_dir / "task_bridge" / "execution_repository_binding.json").exists()
    assert not (run_dir / "experiments" / "sessions").exists()
    assert load_pipeline_jobs(run_dir) == []


@pytest.mark.asyncio
async def test_confirm_route_reports_incomplete_execution_contract_with_stable_code(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_primary_metric_api"
    run_dir.mkdir()
    _prepare_executable_source(run_dir)
    save_research_intent_summary(run_dir, ResearchIntentSummary(goal="比较候选方法"))
    draft = TaskBridge.build_experiment_task(run_dir, user_input="准备实验")
    monkeypatch.setattr(runs_route, "RUNS_ROOT", str(tmp_path))

    with pytest.raises(HTTPException) as excinfo:
        await runs_route.confirm_experiment_task(
            run_dir.name,
            draft.task_id,
            runs_route.ConfirmExperimentTaskRequest(
                execution_mode="agent_assisted_after_approval",
                execution_repository_source_id=EXECUTION_SOURCE_ID,
            ),
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == {
        "code": "execution_contract_incomplete",
        "message": "primary metric is not confirmed",
    }
    assert not (run_dir / "input_task.yaml").exists()
    assert not (run_dir / "experiments" / "sessions").exists()
    assert load_pipeline_jobs(run_dir) == []


def test_confirmed_execution_repository_is_immutable_on_replay(tmp_path: Path):
    run_dir = tmp_path / "run_execution_source_replay"
    run_dir.mkdir()
    task = _confirmed(run_dir)

    with pytest.raises(TaskConfirmationConflict) as excinfo:
        TaskBridge.confirm_or_load_existing(
            run_dir,
            task_id=task.task_id,
            execution_mode="agent_assisted_after_approval",
            execution_repository_source_id="src_other",
        )

    assert excinfo.value.code == "confirmation_invalid"


def test_confirmed_task_rejects_execution_mode_change(tmp_path: Path):
    run_dir = tmp_path / "run_mode_immutable"
    run_dir.mkdir()
    draft = _draft(run_dir)
    confirmed = TaskBridge.confirm_or_load_existing(
        run_dir,
        task_id=draft.task_id,
        execution_mode="plan_only",
    )

    with pytest.raises(TaskConfirmationConflict, match="execution mode differs") as excinfo:
        TaskBridge.confirm_or_load_existing(
            run_dir,
            task_id=confirmed.task_id,
            execution_mode="agent_assisted_after_approval",
        )

    assert excinfo.value.code == "execution_mode_mismatch"
    assert confirmed.execution_mode == "plan_only"
    assert load_pipeline_jobs(run_dir) == []
    assert not (run_dir / "experiments" / "sessions").exists()


def test_existing_confirmed_files_must_match_authoritative_draft(tmp_path: Path):
    run_dir = tmp_path / "run_conflict"
    run_dir.mkdir()
    task = _confirmed(run_dir)
    (run_dir / "input_task.yaml").write_text("run_id: different\n", encoding="utf-8")

    with pytest.raises(TaskConfirmationConflict, match="existing input_task.yaml") as excinfo:
        TaskBridge.confirm_or_load_existing(
            run_dir,
            task_id=task.task_id,
            execution_mode="agent_assisted_after_approval",
        )

    assert excinfo.value.code == "input_task_invalid"


def test_confirmation_reports_task_id_mismatch_with_stable_code(tmp_path: Path):
    run_dir = tmp_path / "run_task_id_mismatch"
    run_dir.mkdir()
    _draft(run_dir)

    with pytest.raises(TaskConfirmationConflict, match="task_id does not match") as excinfo:
        TaskBridge.confirm_or_load_existing(
            run_dir,
            task_id="task_outdated",
            execution_mode="plan_only",
        )

    assert excinfo.value.code == "task_mismatch"


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
            execution_repository_source_id=EXECUTION_SOURCE_ID,
        ),
    )

    assert result.disposition == "created"
    assert result.session_id is not None
    assert result.environment_job_id == "job_000001"


@pytest.mark.asyncio
async def test_confirm_route_reports_a_stale_summary_with_a_stable_conflict_code(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_confirm_stale_summary"
    run_dir.mkdir()
    draft = _draft(run_dir)
    save_research_intent_summary(run_dir, ResearchIntentSummary(goal="用户修订了实验目标"))
    monkeypatch.setattr(runs_route, "RUNS_ROOT", str(tmp_path))

    with pytest.raises(HTTPException) as excinfo:
        await runs_route.confirm_experiment_task(
            run_dir.name,
            draft.task_id,
            runs_route.ConfirmExperimentTaskRequest(execution_mode="plan_only"),
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == {
        "code": "summary_changed",
        "message": "research summary changed after task preparation",
    }
