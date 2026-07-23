"""PR-004A durable Attempt / PipelineJob integration tests."""

from __future__ import annotations

import json
import sys
import time
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


def _poll_until_terminal(run_dir: Path, attempt_id: str) -> None:
    for _ in range(100):
        attempt = ExperimentAttemptStore().load(run_dir, attempt_id)
        if attempt is not None and attempt.runtime_status in {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"}:
            return
        time.sleep(0.02)
        _process_pending_jobs(run_dir)
    raise AssertionError("Attempt did not reach a terminal state")


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


def test_attempt_creation_freezes_protocol_references_in_its_identity(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_protocol_identity"
    session_id = _ready_session(run_dir)
    plan = _plan()
    result = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:protocol-identity",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
        evaluation_contract_ref="contract.json",
        evaluation_contract_sha256="a" * 64,
        protected_artifact_report_ref="protected_hashes.json",
        protected_artifact_report_sha256="b" * 64,
    )
    assert result.attempt.evaluation_contract_ref == "contract.json"
    assert result.attempt.protected_artifact_report_ref == "protected_hashes.json"
    with pytest.raises(ValueError, match="different Attempt identity"):
        ExperimentAttemptService().create_or_get_attempt(
            run_dir,
            session_id=session_id,
            job_type="experiment_baseline",
            idempotency_key="baseline:protocol-identity",
            command_plan=plan,
            input_refs=_refs(plan),
            job_timeout_sec=60,
            evaluation_contract_ref="contract.json",
            evaluation_contract_sha256="c" * 64,
            protected_artifact_report_ref="protected_hashes.json",
            protected_artifact_report_sha256="b" * 64,
        )


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
    _poll_until_terminal(run_dir, started.attempt.attempt_id)
    finished = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert finished is not None
    assert finished.runtime_status == "COMPLETED"
    assert finished.execution_result_ref == "attempts/attempt_000001/execution_result.json"
    assert (run_dir / "attempts" / "attempt_000001" / "metrics.json").is_file()
    assert not (run_dir / "attempts" / "attempt_000001" / "health_diagnosis.json").exists()
    assert load_pipeline_jobs(run_dir)[0]["status"] == "completed"
    assert "experiment.attempt.finalized" in {
        event["type"] for event in load_events_since(run_dir)
    }


def test_cpu_attempt_hides_inherited_cuda_devices(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_attempt_cpu_visibility"
    session_id = _ready_session(run_dir)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "7")
    plan = _plan(command="import os\nfrom pathlib import Path\nPath('cuda.txt').write_text(os.environ.get('CUDA_VISIBLE_DEVICES', 'missing'))\nPath('metrics.json').write_text('{}')")
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:cpu-visibility",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
        required_device_count=0,
        required_vram_mb=0,
    )
    assert _process_pending_jobs(run_dir) == 1
    _poll_until_terminal(run_dir, started.attempt.attempt_id)
    assert (run_dir / "attempts" / started.attempt.attempt_id / "cuda.txt").read_text(encoding="utf-8") == ""


def test_zero_exit_without_expected_output_is_not_scientifically_evaluable(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_missing_output"
    session_id = _ready_session(run_dir)
    plan = _plan(command="pass")
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:missing-output",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
    )
    assert _process_pending_jobs(run_dir) == 1
    _poll_until_terminal(run_dir, started.attempt.attempt_id)
    final = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    outcome = json.loads((run_dir / "attempts" / started.attempt.attempt_id / "outcome_card.json").read_text(encoding="utf-8"))
    assert final is not None and final.failure_code == "RUN_EXPECTED_OUTPUT_MISSING"
    assert outcome["attempt_category"] == "run_failed"


def test_hung_process_reaches_timeout_termination_path(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_timeout"
    session_id = _ready_session(run_dir)
    plan = _plan(command="import time\ntime.sleep(5)")
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:timeout",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=1,
    )
    assert _process_pending_jobs(run_dir) == 1
    time.sleep(1.1)
    _poll_until_terminal(run_dir, started.attempt.attempt_id)
    final = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert final is not None and final.runtime_status == "TIMED_OUT"
    assert final.failure_code == "RUN_TIMEOUT"
    assert not (run_dir / "attempts" / started.attempt.attempt_id / "health_diagnosis.json").exists()


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
    _poll_until_terminal(run_dir, started.attempt.attempt_id)

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

    import autoad_researcher.experiment.attempt_execution as execution_module

    monkeypatch.setattr(execution_module, "GpuAllocator", UnavailableAllocator)
    assert _process_pending_jobs(run_dir) == 1

    attempt = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert attempt is not None
    assert attempt.runtime_status == "FAILED"
    assert attempt.failure_code == "TEMPORARY_GPU_UNAVAILABLE"
    assert load_pipeline_jobs(run_dir)[0]["status"] == "failed"


def test_transient_classification_automatically_queues_one_bounded_retry(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_automatic_retry"
    session_id = _ready_session(run_dir)
    plan = _plan().model_copy(update={"program": "/definitely/not/an/executable"})
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:auto-retry",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
        max_retries=1,
    )
    assert _process_pending_jobs(run_dir) == 1
    parent = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert parent is not None and parent.failure_code == "PROCESS_SPAWN_FAILED"
    jobs = load_pipeline_jobs(run_dir)
    assert len(jobs) == 2
    retry = ExperimentAttemptStore().load(run_dir, "attempt_000002")
    assert retry is not None
    assert retry.retry_of == parent.attempt_id
    assert retry.retry_count == 1


def test_popen_attempt_does_not_block_worker_and_can_be_cancelled(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_cancel"
    session_id = _ready_session(run_dir)
    plan = _plan(command="import time\ntime.sleep(5)")
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:cancel",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
    )

    started_at = time.monotonic()
    assert _process_pending_jobs(run_dir) == 1
    assert time.monotonic() - started_at < 1
    active = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert active is not None and active.runtime_status == "RUNNING"
    assert active.pid is not None and active.process_group_id is not None
    ExperimentAttemptStore().request_cancel(run_dir, attempt_id=started.attempt.attempt_id)
    _poll_until_terminal(run_dir, started.attempt.attempt_id)
    cancelled = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert cancelled is not None and cancelled.runtime_status == "CANCELLED"
    assert load_pipeline_jobs(run_dir)[0]["status"] == "failed"


def test_termination_escalates_from_term_to_kill_for_process_ignoring_term(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_termination_escalation"
    session_id = _ready_session(run_dir)
    plan = _plan(
        command=(
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: None)\n"
            "while True: time.sleep(0.01)"
        )
    )
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:termination-escalation",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
        termination_grace_seconds=1,
    )
    assert _process_pending_jobs(run_dir) == 1
    ExperimentAttemptStore().request_cancel(run_dir, attempt_id=started.attempt.attempt_id)
    _process_pending_jobs(run_dir)
    terminating = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert terminating is not None
    assert terminating.runtime_status == "TERMINATING"
    assert terminating.termination_reason == "USER_CANCELLED"
    assert terminating.termination_requested_at is not None
    _poll_until_terminal(run_dir, started.attempt.attempt_id)
    final = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert final is not None and final.runtime_status == "CANCELLED"


def test_worker_restart_keeps_observing_persisted_process(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_worker_restart"
    session_id = _ready_session(run_dir)
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:worker-restart",
        command_plan=_plan(command="import time\ntime.sleep(5)"),
        input_refs=_refs(_plan(command="import time\ntime.sleep(5)")),
        job_timeout_sec=60,
    )
    assert _process_pending_jobs(run_dir) == 1
    import autoad_researcher.experiment.attempt_execution as execution

    execution._PROCESSES.clear()
    assert _process_pending_jobs(run_dir) == 0
    recovered = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert recovered is not None and recovered.runtime_status == "RUNNING"
    ExperimentAttemptStore().request_cancel(run_dir, attempt_id=started.attempt.attempt_id)
    _poll_until_terminal(run_dir, started.attempt.attempt_id)


def test_checkpoint_stall_is_explicitly_configured_and_stops_attempt(tmp_path: Path):
    run_dir = tmp_path / "run_attempt_checkpoint_stall"
    session_id = _ready_session(run_dir)
    plan = _plan(command="from pathlib import Path\nimport time\nPath('checkpoint.json').write_text('{}')\nwhile True:\n print('stdout remains active', flush=True)\n time.sleep(0.02)")
    started = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session_id,
        job_type="experiment_baseline",
        idempotency_key="baseline:checkpoint-stall",
        command_plan=plan,
        input_refs=_refs(plan),
        job_timeout_sec=60,
        checkpoint_watch_path="checkpoint.json",
        checkpoint_stall_seconds=1,
    )
    assert _process_pending_jobs(run_dir) == 1
    time.sleep(1.1)
    _process_pending_jobs(run_dir)
    _poll_until_terminal(run_dir, started.attempt.attempt_id)
    events = (run_dir / "attempts" / started.attempt.attempt_id / "health_events.jsonl").read_text(encoding="utf-8")
    final = ExperimentAttemptStore().load(run_dir, started.attempt.attempt_id)
    assert "CHECKPOINT_STALLED" in events
    assert final is not None and final.failure_code == "CHECKPOINT_STALLED"
