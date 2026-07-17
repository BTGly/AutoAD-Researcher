from __future__ import annotations

import sys
from pathlib import Path

from autoad_researcher.experiment.attempt_service import ExperimentAttemptService
from autoad_researcher.experiment.coordinator_recovery import (
    CoordinatorCheckpointStore,
    CoordinatorRecoveryService,
    new_checkpoint,
)
from autoad_researcher.experiment.idea_tree import IdeaTreeStore
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.runner import ExperimentCommandPlan, ExperimentInputRefs, experiment_command_sha256


def _ready_session(run_dir: Path) -> str:
    sessions = ExperimentSessionStore()
    session, _ = sessions.create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash="c" * 64,
        execution_mode="agent_assisted_after_approval",
    )
    sessions.update_environment_state(
        run_dir,
        session_id=session.session_id,
        status="READY_FOR_BASELINE",
        environment_status="ready",
        readiness_status="ready",
        readiness_blockers=[],
    )
    IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)
    return session.session_id


def _plan() -> ExperimentCommandPlan:
    return ExperimentCommandPlan(
        schema_version=1,
        command_id="recovery_fixture",
        program=sys.executable,
        args=["-c", "pass"],
        cwd="attempts/attempt_000001",
        environment={},
        timeout_seconds=30,
        network=False,
        expected_outputs=["metrics.json"],
    )


def _refs(plan: ExperimentCommandPlan) -> ExperimentInputRefs:
    return ExperimentInputRefs(
        repository_fingerprint="recovery-fixture",
        environment_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        asset_manifest_sha256="c" * 64,
        command_sha256=experiment_command_sha256(plan),
    )


def test_recovery_rebuilds_when_checkpoint_missing_and_resumes_when_revision_matches(tmp_path: Path):
    session_id = _ready_session(tmp_path)
    service = CoordinatorRecoveryService()
    missing = service.recover(tmp_path, session_id=session_id)
    assert missing.checkpoint_action == "rebuild_from_authority"
    assert missing.observation_recovery.action == "reobserve"

    CoordinatorCheckpointStore().write(
        tmp_path,
        session_id=session_id,
        checkpoint=new_checkpoint(checkpoint_ref="deepagents/thread/session", tree_revision=0),
    )
    matched = service.recover(tmp_path, session_id=session_id)
    assert matched.checkpoint_action == "resume_checkpoint"


def test_recovery_rejects_checkpoint_when_authoritative_tree_revision_changed(tmp_path: Path):
    session_id = _ready_session(tmp_path)
    CoordinatorCheckpointStore().write(
        tmp_path,
        session_id=session_id,
        checkpoint=new_checkpoint(checkpoint_ref="deepagents/thread/session", tree_revision=0),
    )
    IdeaTreeStore().add_node(
        tmp_path,
        session_id=session_id,
        expected_revision=0,
        idempotency_key="recovery:tree-change",
        parent_id="idea_000000",
        mechanism="changed mechanism",
        hypothesis="changed hypothesis",
        observable="score",
        grounding=[],
        expected_cost="low",
    )
    result = CoordinatorRecoveryService().recover(tmp_path, session_id=session_id)
    assert result.checkpoint_action == "rebuild_from_authority"
    assert "stale" in result.checkpoint_reason


def test_recovery_reconnects_pending_attempt_after_its_job_record_is_missing(tmp_path: Path):
    session_id = _ready_session(tmp_path)
    plan = _plan()
    started = ExperimentAttemptService().create_or_get_attempt(
        tmp_path,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="recovery:pending",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=30,
    )
    jobs_path = tmp_path / "jobs" / "pipeline_jobs.jsonl"
    jobs_path.unlink()

    result = CoordinatorRecoveryService().recover(tmp_path, session_id=session_id)
    assert [item.model_dump() for item in result.pending_attempts] == [
        {"attempt_id": started.attempt.attempt_id, "disposition": "repaired", "pipeline_job_id": "job_000001"}
    ]
    assert jobs_path.is_file()


def test_recovery_leaves_existing_pending_attempt_job_connected(tmp_path: Path):
    session_id = _ready_session(tmp_path)
    plan = _plan()
    started = ExperimentAttemptService().create_or_get_attempt(
        tmp_path,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="recovery:connected",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=30,
    )
    result = CoordinatorRecoveryService().recover(tmp_path, session_id=session_id)
    assert result.pending_attempts[0].disposition == "already_connected"
    assert result.pending_attempts[0].pipeline_job_id == started.attempt.pipeline_job_id
