"""Pytest E2E coverage for confirmed-task, attempt, and health boundaries.

The predecessor was an import-time script.  These tests retain its useful
cross-component intent while allowing pytest to collect and report failures.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.experiment.starter import ExperimentStarter
from autoad_researcher.assistant.v2.execution_repository import ExecutionRepositoryBinding
from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.assistant.v2.task_bridge import ExperimentTaskDraft, InputTask
from autoad_researcher.experiment.attempt import ExperimentAttempt
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.failure_classifier import classify_or_load
from autoad_researcher.experiment.finalizer import finalize_attempt
from autoad_researcher.experiment.gpu import GpuAllocator
from autoad_researcher.experiment.watchdog import RuntimeWatchdog
from autoad_researcher.runner.models import ExperimentCommandPlan, ExperimentInputRefs


def _draft(run_dir: Path) -> tuple[ExperimentTaskDraft, InputTask]:
    task = InputTask(run_id="e2e_test", request="confirmed baseline", source_ids=[], user_idea="baseline", constraints=["no eval changes"])
    binding = ExecutionRepositoryBinding(
        source_id="src_e2e",
        source_kind="local_repo",
        repository_ref="repos/src_e2e",
        repository_fingerprint="a" * 64,
        attestation_ref="repo_acquisition/src_e2e/repository_attestation.json",
        attestation_sha256="b" * 64,
        adapter_manifest_ref="repos/src_e2e/autoad_executor_adapter.json",
        adapter_manifest_sha256="c" * 64,
        adapter_id="generic_python",
        adapter_evidence={},
    )
    binding_path = run_dir / "task_bridge" / "execution_repository_binding.json"
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    binding_path.write_text(binding.model_dump_json(), encoding="utf-8")
    return ExperimentTaskDraft(task_id="task_e2e", run_id="e2e_test", status="confirmed", execution_mode="agent_assisted_after_approval", input_task=task, summary_sha256=sha256(json.dumps(task.model_dump(mode="json"), sort_keys=True).encode()).hexdigest(), execution_repository_binding=binding, created_at=datetime.now(timezone.utc).isoformat(), confirmed_at=datetime.now(timezone.utc).isoformat()), task


def _attempt(run_dir: Path, key: str = "e2e") -> ExperimentAttempt:
    now = datetime.now(timezone.utc).isoformat()
    plan = ExperimentCommandPlan(schema_version=1, command_id=f"cmd_{key}", program=sys.executable, args=["-c", "print('ok')"], cwd="attempts/attempt_000001", environment={}, timeout_seconds=30, network=False, expected_outputs=["metrics.json"])
    refs = ExperimentInputRefs(repository_fingerprint="e2e", environment_sha256="a" * 64, dataset_manifest_sha256="b" * 64, asset_manifest_sha256="c" * 64, command_sha256="d" * 64)
    return ExperimentAttempt(attempt_id="attempt_000000", run_id=run_dir.name, session_id="session_e2e", idempotency_key=key, job_type="experiment_attempt", attempt_purpose="exploration", command_plan=plan, input_refs=refs, job_timeout_sec=30, created_at=now, updated_at=now)


def test_confirmed_task_is_idempotently_connected_to_environment_job(tmp_path: Path):
    draft, task = _draft(tmp_path); (tmp_path / "input_task.yaml").write_text(json.dumps(task.model_dump(mode="json")), encoding="utf-8")
    first = ExperimentStarter().on_task_confirmed(tmp_path, draft, execution_mode="agent_assisted_after_approval")
    replay = ExperimentStarter().on_task_confirmed(tmp_path, draft, execution_mode="agent_assisted_after_approval")
    assert (first.disposition, replay.disposition) == ("created", "reused")
    assert len([job for job in load_pipeline_jobs(tmp_path) if job["job_type"] == "experiment_environment_prepare"]) == 1


def test_attempt_persists_legal_lifecycle_and_rejects_illegal_finalization(tmp_path: Path):
    store = ExperimentAttemptStore(); attempt, created = store.create_or_get(tmp_path, _attempt(tmp_path))
    assert created
    store.bind_pipeline_job(tmp_path, attempt_id=attempt.attempt_id, pipeline_job_id="job_000001")
    store.mark_starting(tmp_path, attempt_id=attempt.attempt_id, pipeline_job_id="job_000001")
    running = store.mark_running(tmp_path, attempt_id=attempt.attempt_id, pid=os.getpid(), process_group_id=os.getpid())
    assert running.runtime_status == "RUNNING"
    finished = store.finish(tmp_path, attempt_id=attempt.attempt_id, runtime_status="COMPLETED", failure_code=None, execution_result_ref="attempts/attempt_000001/execution_result.json")
    assert ExperimentAttemptStore().load(tmp_path, attempt.attempt_id) == finished
    with pytest.raises(ValueError, match="already finalized"):
        store.finish(tmp_path, attempt_id=attempt.attempt_id, runtime_status="FAILED", failure_code="OOM", execution_result_ref="other.json")


def test_watchdog_returns_first_event_once_and_classifier_uses_durable_evidence(tmp_path: Path):
    attempt_dir = tmp_path / "attempts" / "attempt_000001"; attempt_dir.mkdir(parents=True)
    (attempt_dir / "stderr.log").write_text("CUDA out of memory\n", encoding="utf-8")
    watchdog = RuntimeWatchdog()
    assert [event.event for event in watchdog.inspect(attempt_dir, pid=None)] == ["OOM_DETECTED"]
    assert watchdog.inspect(attempt_dir, pid=None) == []
    assert classify_or_load(attempt_dir).failure_code == "OOM"
    (attempt_dir / "heartbeat.json").write_text(json.dumps({"timestamp": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}), encoding="utf-8")
    assert "STALE_HEARTBEAT" in {event.event for event in RuntimeWatchdog(heartbeat_interval_seconds=15).inspect(attempt_dir, pid=None)}


def test_finalizer_excludes_invalid_metrics_and_produces_valid_outcome(tmp_path: Path):
    bad = tmp_path / "attempts" / "bad"; bad.mkdir(parents=True); (bad / "execution_result.json").write_text("{}", encoding="utf-8")
    assert finalize_attempt(bad, attempt_id="attempt_000001", runtime_status="COMPLETED").attempt_category == "run_failed"
    good = tmp_path / "attempts" / "good"; good.mkdir(); (good / "execution_result.json").write_text("{}", encoding="utf-8"); (good / "metrics.json").write_text(json.dumps({"score": 1}), encoding="utf-8")
    assert finalize_attempt(good, attempt_id="attempt_000002", runtime_status="COMPLETED").attempt_category == "scientifically_evaluable"


def test_corrupt_attempt_is_rejected_and_invalid_gpu_request_is_not_allocated(tmp_path: Path):
    store = ExperimentAttemptStore(); attempt, _ = store.create_or_get(tmp_path, _attempt(tmp_path, "corrupt"))
    attempt_path = tmp_path / "experiments" / "attempts" / f"{attempt.attempt_id}.json"; attempt_path.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError): store.load(tmp_path, attempt.attempt_id)
    with pytest.raises(ValueError): GpuAllocator().allocate(tmp_path, attempt_id="attempt_gpu", worker_id="e2e", required_device_count=0, required_vram_mb=1)
