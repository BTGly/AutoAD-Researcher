"""PR-004A durable Attempt / PipelineJob integration tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.event_service import load_events_since
from autoad_researcher.assistant.v2.job_service import claim_pipeline_job, load_pipeline_jobs
from autoad_researcher.experiment.attempt_service import ExperimentAttemptService
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.gpu import GpuUnavailableError
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.runner import ExperimentCommandPlan, ExperimentInputRefs, experiment_command_sha256
from autoad_researcher.worker.main import _process_pending_jobs


def _ready_session(run_dir: Path) -> str:
    store = ExperimentSessionStore()
    session, _ = store.create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash="a" * 64,
        execution_mode="agent_assisted_after_approval",
    )
    store.update_environment_state(
        run_dir,
        session_id=session.session_id,
        status="READY_FOR_BASELINE",
        environment_status="ready",
        readiness_status="ready",
        readiness_blockers=[],
    )
    return session.session_id


def _plan(*, attempt_id: str = "attempt_000001", command: str | None = None) -> ExperimentCommandPlan:
    plan = ExperimentCommandPlan(
        schema_version=1,
        command_id="baseline_fixture",
        program=sys.executable,
        args=[
            "-c",
            command
            or "from pathlib import Path\nPath('metrics.json').write_text('{}')",
        ],
        cwd=f"attempts/{attempt_id}",
        environment={},
        timeout_seconds=30,
        network=False,
        expected_outputs=["metrics.json"],
    )
    return plan


def _refs(plan: ExperimentCommandPlan) -> ExperimentInputRefs:
    return ExperimentInputRefs(
        repository_fingerprint="fixture-repository",
        environment_sha256="b" * 64,
        dataset_manifest_sha256="c" * 64,
        asset_manifest_sha256="d" * 64,
        command_sha256=experiment_command_sha256(plan),
    )


def test_attempt_replay_creates_one_attempt_and_one_pipeline_job(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_replay"
    session_id = _ready_session(run_dir)
    service = ExperimentAttemptService()
    plan = _plan()

    first = service.create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:session:0",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
        max_retries=1,
    )
    second = service.create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:session:0",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
        max_retries=1,
    )

    assert first.disposition == "created"
    assert second.disposition == "reused"
    assert first.attempt.attempt_id == "attempt_000001"
    assert first.attempt.pipeline_job_id == second.attempt.pipeline_job_id
    assert len(load_pipeline_jobs(run_dir)) == 1


def test_two_workers_only_one_claims_attempt_job(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_claim"
    session_id = _ready_session(run_dir)
    plan = _plan()
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:claim",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
    )

    assert claim_pipeline_job(run_dir, started.pipeline_job["job_id"]) is not None
    assert claim_pipeline_job(run_dir, started.pipeline_job["job_id"]) is None


def test_worker_dispatches_fixture_command_and_finalizes_attempt(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_worker"
    session_id = _ready_session(run_dir)
    plan = _plan()
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:fixture",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
    )

    assert _process_pending_jobs(run_dir) == 1
    finished = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert finished is not None
    assert finished.runtime_status == "COMPLETED"
    assert finished.execution_result_ref == "attempts/attempt_000001/execution_result.json"
    assert (run_dir / "attempts" / "attempt_000001" / "metrics.json").is_file()
    assert load_pipeline_jobs(run_dir)[0]["status"] == "completed"
    assert "experiment.attempt.finalized" in {
        event["type"] for event in load_events_since(run_dir)
    }


def test_failed_attempt_retry_has_lineage_backoff_and_new_job(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_retry"
    session_id = _ready_session(run_dir)
    plan = _plan(command="import sys\nsys.exit(2)")
    service = ExperimentAttemptService()
    started = service.create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:retry",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
        max_retries=1,
    )
    assert _process_pending_jobs(run_dir) == 1

    retry = service.create_retry(run_dir, attempt_id=started.attempt.attempt_id)
    assert retry.attempt.retry_of == started.attempt.attempt_id
    assert retry.attempt.retry_count == 1
    assert retry.attempt.retry_not_before is not None
    assert retry.attempt.command_plan.cwd == "attempts/attempt_000002"
    assert retry.attempt.input_refs.command_sha256 == experiment_command_sha256(retry.attempt.command_plan)
    assert len(load_pipeline_jobs(run_dir)) == 2
    assert _process_pending_jobs(run_dir) == 0

    ExperimentAttemptStore().finish(
        run_dir,
        attempt_id=retry.attempt.attempt_id,
        runtime_status="FAILED",
        failure_code="RUN_COMMAND_FAILED",
        execution_result_ref="attempts/attempt_000002/execution_result.json",
    )

    with pytest.raises(ValueError, match="retry limit exhausted"):
        service.create_retry(run_dir, attempt_id=retry.attempt.attempt_id)


def test_plan_only_session_cannot_create_attempt(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_plan_only"
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash="e" * 64,
        execution_mode="plan_only",
    )
    plan = _plan()

    with pytest.raises(ValueError, match="plan_only"):
        ExperimentAttemptService().create_or_get_attempt(
            run_dir,
            session_id=session.session_id,
            job_type="experiment_baseline",
            idempotency_key="baseline:plan-only",
            command_plan=plan,
            input_refs=_refs(plan),
            job_timeout_sec=60,
        )


def test_gpu_capacity_failure_finalizes_attempt_without_starting_training(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_attempt_gpu_unavailable"
    session_id = _ready_session(run_dir)
    plan = _plan()
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:gpu-unavailable",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
        required_device_count=1,
        required_vram_mb=10_000,
    )

    class UnavailableAllocator:
        def allocate(self, *args, **kwargs):
            raise GpuUnavailableError("TEMPORARY_GPU_UNAVAILABLE: fixture")

    import autoad_researcher.experiment.gpu as gpu_module

    monkeypatch.setattr(gpu_module, "GpuAllocator", UnavailableAllocator)
    assert _process_pending_jobs(run_dir) == 1

    attempt = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert attempt is not None
    assert attempt.runtime_status == "FAILED"
    assert attempt.failure_code == "TEMPORARY_GPU_UNAVAILABLE"
    assert load_pipeline_jobs(run_dir)[0]["status"] == "failed"
