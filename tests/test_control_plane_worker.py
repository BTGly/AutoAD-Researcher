from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoad_researcher.core.control_plane import (
    CorruptAuthoritativeStore,
    JobClaimFenceError,
    PipelineJobStore,
)
from autoad_researcher.core.control_plane.io import atomic_write_jsonl
from autoad_researcher.worker.main import _process_pending_jobs


def _run_dir(tmp_path: Path, name: str = "run_worker") -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir()
    return run_dir


def _enqueue(store: PipelineJobStore, source_id: str, *, job_type: str = "web_search"):
    return store.enqueue(
        source_id=source_id,
        job_type=job_type,
        evidence_role="candidate_source_only",
        payload={"query": source_id},
    )


def _attempt_dir(run_dir: Path, job_id: str, attempt_count: int, claim_token: str) -> Path:
    return (
        run_dir
        / "experiment_agents"
        / "attempts"
        / job_id
        / f"attempt_{attempt_count}_{claim_token}"
    )


def test_claim_is_stable_and_persists_identity_before_running(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    store = PipelineJobStore(run_dir)
    first = _enqueue(store, "src_first")
    second = _enqueue(store, "src_second")

    # Physical JSONL order is not the scheduling order.
    atomic_write_jsonl(
        store.path,
        [job.model_dump(mode="json", exclude_none=True) for job in reversed(store.list())],
    )
    claimed = store.claim_next(worker_id="worker_test")

    assert claimed is not None
    assert claimed.job_id == first.job_id
    assert claimed.job_id != second.job_id
    assert claimed.attempt_count == 1
    assert claimed.claim_token is not None
    claim_path = _attempt_dir(run_dir, claimed.job_id, 1, claimed.claim_token) / "claim.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    assert claim["job_id"] == first.job_id
    assert claim["worker_id"] == "worker_test"
    assert claim["claim_token"] == claimed.claim_token


def test_fenced_completion_requires_current_claim_and_writes_attempt_result(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    store = PipelineJobStore(run_dir)
    job = _enqueue(store, "src")
    claimed = store.claim_next(worker_id="worker_test")
    assert claimed is not None and claimed.claim_token is not None

    with pytest.raises(JobClaimFenceError):
        store.complete(
            job.job_id,
            claim_token="claim_00000000000000000000000000000000",
            expected_attempt_count=claimed.attempt_count,
        )

    completed = store.complete(
        job.job_id,
        claim_token=claimed.claim_token,
        expected_attempt_count=claimed.attempt_count,
        outputs=["sources/src/result.json"],
    )
    result_path = (
        _attempt_dir(run_dir, job.job_id, claimed.attempt_count, claimed.claim_token)
        / "attempt_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert completed.status == "completed"
    assert result["status"] == "completed"
    assert result["claim_token"] == claimed.claim_token


def test_orphan_claim_is_closed_as_claim_aborted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run_dir = _run_dir(tmp_path)
    store = PipelineJobStore(run_dir)
    job = _enqueue(store, "src")
    original_write = store._write_unlocked

    def crash_before_job_state(jobs):
        if any(item.status == "running" for item in jobs):
            raise RuntimeError("simulated crash")
        original_write(jobs)

    monkeypatch.setattr(store, "_write_unlocked", crash_before_job_state)
    with pytest.raises(RuntimeError, match="simulated crash"):
        store.claim_next(worker_id="worker_crash")
    monkeypatch.setattr(store, "_write_unlocked", original_write)

    results = store.reconcile_orphan_claims()
    assert len(results) == 1
    assert results[0].job_id == job.job_id
    assert results[0].status == "claim_aborted"
    assert store.get(job.job_id).attempt_count == 0  # type: ignore[union-attr]


def test_lease_recovery_uses_backoff_then_fails_on_fourth_expiry(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    store = PipelineJobStore(run_dir)
    job = _enqueue(store, "experiment", job_type="experiment_prepare")
    current = datetime(2026, 7, 13, tzinfo=timezone.utc)

    expected_delays = [5, 15, 45]
    for recovery_index, delay in enumerate(expected_delays, 1):
        claimed = store.claim_next(worker_id="worker_test", now=current)
        assert claimed is not None and claimed.claim_token is not None
        expiry = current + timedelta(seconds=301)
        transitions = store.requeue_expired(now=expiry)
        assert transitions[0].to_status == "queued"
        updated = store.get(job.job_id)
        assert updated is not None
        assert updated.consecutive_lease_expiry_count == recovery_index
        assert updated.next_eligible_at == expiry + timedelta(seconds=delay)
        result_path = (
            _attempt_dir(run_dir, job.job_id, claimed.attempt_count, claimed.claim_token)
            / "attempt_result.json"
        )
        assert json.loads(result_path.read_text(encoding="utf-8"))["status"] == "lease_lost"
        current = expiry + timedelta(seconds=delay)

    claimed = store.claim_next(worker_id="worker_test", now=current)
    assert claimed is not None
    transitions = store.requeue_expired(now=current + timedelta(seconds=301))
    final = store.get(job.job_id)
    assert transitions[0].reason == "repeated_lease_expiry"
    assert final is not None
    assert final.status == "failed"
    assert final.error == "repeated_lease_expiry"
    assert final.consecutive_lease_expiry_count == 4


def test_stale_input_recovery_records_both_hashes_and_backoff(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    store = PipelineJobStore(run_dir)
    job = _enqueue(store, "experiment", job_type="experiment_prepare")
    current = datetime(2026, 7, 13, tzinfo=timezone.utc)
    claimed = store.claim_next(worker_id="worker_test", now=current)
    assert claimed is not None and claimed.claim_token is not None

    transition = store.requeue_stale_input(
        job.job_id,
        claim_token=claimed.claim_token,
        expected_attempt_count=claimed.attempt_count,
        input_sha256="a" * 64,
        publication_check_input_sha256="b" * 64,
        now=current + timedelta(seconds=1),
    )

    assert transition.reason == "stale_input"
    queued = store.get(job.job_id)
    assert queued is not None
    assert queued.status == "queued"
    assert queued.consecutive_stale_count == 1
    assert queued.next_eligible_at == current + timedelta(seconds=6)
    result_path = (
        _attempt_dir(run_dir, job.job_id, claimed.attempt_count, claimed.claim_token)
        / "attempt_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "stale_input"
    assert result["input_sha256"] == "a" * 64
    assert result["publication_check_input_sha256"] == "b" * 64


def test_dependency_reconciliation_detects_historical_cycle(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    store = PipelineJobStore(run_dir)
    first = _enqueue(store, "first")
    second = store.enqueue(
        source_id="second",
        job_type="web_search",
        evidence_role="candidate_source_only",
        payload={"query": "second", "depends_on": first.job_id},
    )
    jobs = store.list()
    jobs[0] = jobs[0].model_copy(update={"payload": {"depends_on": second.job_id}})
    atomic_write_jsonl(
        store.path,
        [job.model_dump(mode="json", exclude_none=True) for job in jobs],
    )

    transitions = store.reconcile_job_dependencies()
    assert {transition.job_id for transition in transitions} == {first.job_id, second.job_id}
    assert {transition.reason for transition in transitions} == {"dependency_cycle"}
    assert {job.status for job in store.list()} == {"failed"}


def test_worker_continues_authoritative_jobs_when_audit_is_corrupt(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    store = PipelineJobStore(run_dir)
    job = _enqueue(store, "src", job_type="unknown_type")
    events_path = run_dir / "events" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text("{truncated\n", encoding="utf-8")

    assert _process_pending_jobs(run_dir, worker_id="worker_test") == 1
    failed = store.get(job.job_id)
    assert failed is not None and failed.status == "failed"
    health = json.loads((run_dir / "events" / "audit_health.json").read_text(encoding="utf-8"))
    assert health["status"] == "degraded"
    assert events_path.read_text(encoding="utf-8") == "{truncated\n"


def test_worker_fails_closed_on_corrupt_authoritative_jobs(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    path = run_dir / "jobs" / "pipeline_jobs.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{truncated\n", encoding="utf-8")

    with pytest.raises(CorruptAuthoritativeStore):
        _process_pending_jobs(run_dir, worker_id="worker_test")
