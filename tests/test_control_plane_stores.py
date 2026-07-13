from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.contract_hashing import (
    confirmation_draft_sha256,
    confirmed_contract_sha256,
)
from autoad_researcher.assistant.v2.intent_contract import ResearchIntentContract
from autoad_researcher.core.control_plane import (
    ControlPlaneEventStore,
    ControlPlaneLockReentryError,
    ControlPlaneUnitOfWork,
    CorruptAuditProjection,
    CorruptAuthoritativeStore,
    EventIdempotencyConflict,
    IdempotencyConflict,
    PipelineJobStore,
    resolve_control_plane_path,
    validate_control_plane_store,
)


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run_control"
    run_dir.mkdir()
    return run_dir


def test_event_store_is_strict_durable_and_idempotent(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    store = ControlPlaneEventStore(run_dir)

    first = store.append_once("session.created", "session.created:exp_1", {"session_id": "exp_1"})
    replay = store.append_once("session.created", "session.created:exp_1", {"session_id": "exp_1"})

    assert replay.event_id == first.event_id
    assert [event.event_id for event in store.read_since()] == [1]
    with pytest.raises(EventIdempotencyConflict):
        store.append_once("session.created", "session.created:exp_1", {"session_id": "exp_2"})

    with (run_dir / "events" / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{truncated\n")
    with pytest.raises(CorruptAuditProjection, match=":2"):
        store.read_since()


def test_job_store_allocates_ids_inside_one_lock(tmp_path: Path):
    run_dir = _run_dir(tmp_path)

    def enqueue(index: int) -> str:
        job = PipelineJobStore(run_dir).enqueue(
            source_id=f"src_{index}",
            job_type="web_search",
            evidence_role="candidate_source_only",
            payload={"query": f"query {index}"},
        )
        return job.job_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        job_ids = list(pool.map(enqueue, range(20)))

    assert len(set(job_ids)) == 20
    assert sorted(job_ids) == [f"job_{index:06d}" for index in range(1, 21)]


def test_job_idempotency_detects_request_conflict(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    store = PipelineJobStore(run_dir)
    first = store.enqueue(
        source_id="experiment:exp_1",
        job_type="experiment_prepare",
        evidence_role="experiment_readiness",
        payload={"session_id": "exp_1"},
        idempotency_key="experiment_prepare:abc",
    )
    replay = store.enqueue(
        source_id="experiment:exp_1",
        job_type="experiment_prepare",
        evidence_role="experiment_readiness",
        payload={"session_id": "exp_1"},
        idempotency_key="experiment_prepare:abc",
    )
    assert replay.job_id == first.job_id

    with pytest.raises(IdempotencyConflict):
        store.enqueue(
            source_id="experiment:exp_1",
            job_type="experiment_prepare",
            evidence_role="experiment_readiness",
            payload={"session_id": "exp_other"},
            idempotency_key="experiment_prepare:abc",
        )


def test_job_store_rejects_unknown_dependency_and_corrupt_rows(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    store = PipelineJobStore(run_dir)
    with pytest.raises(ValueError, match="unknown job"):
        store.enqueue(
            source_id="src_1",
            job_type="repo_summarize",
            evidence_role="repo_acquired",
            payload={"depends_on": "job_999999"},
        )

    path = run_dir / "jobs" / "pipeline_jobs.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{bad\n", encoding="utf-8")
    with pytest.raises(CorruptAuthoritativeStore, match=":1"):
        store.list()


def test_unit_of_work_rejects_public_store_lock_reentry(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    with ControlPlaneUnitOfWork(run_dir) as uow:
        with pytest.raises(ControlPlaneLockReentryError):
            uow.jobs.list()


def test_run_relative_path_rejects_escape_and_symlink(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    assert resolve_control_plane_path(run_dir, "experiment_agents/readiness.json") == (
        run_dir / "experiment_agents" / "readiness.json"
    ).resolve()
    with pytest.raises(ValueError):
        resolve_control_plane_path(run_dir, "../outside")
    with pytest.raises(ValueError):
        resolve_control_plane_path(run_dir, "/tmp/outside")

    outside = tmp_path / "outside"
    outside.mkdir()
    (run_dir / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        resolve_control_plane_path(run_dir, "link/file.json")


def test_contract_hashes_ignore_derived_state_but_keep_separate_domains():
    contract = ResearchIntentContract(
        run_id="run_contract",
        research_goal=" improve PatchCore ",
        baseline="PatchCore",
        dataset="MVTec AD",
        primary_metrics=["image_level_auroc", "image_level_auroc"],
        success_criteria="improve image AUROC",
        ready_for_plan=True,
        missing_required_fields=[],
    )
    changed_derived = contract.model_copy(update={
        "ready_for_plan": False,
        "ready_for_repo_analysis": True,
        "missing_required_fields": ["derived_only"],
    })

    assert confirmation_draft_sha256(contract) == confirmation_draft_sha256(changed_derived)
    assert confirmed_contract_sha256(contract) == confirmed_contract_sha256(changed_derived)
    assert confirmation_draft_sha256(contract) != confirmed_contract_sha256(contract)


def test_control_plane_validator_reports_counts(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    ControlPlaneEventStore(run_dir).append("run.checked", {})
    PipelineJobStore(run_dir).enqueue(
        source_id="src_1",
        job_type="web_search",
        evidence_role="candidate_source_only",
        payload={"query": "PatchCore"},
    )
    assert validate_control_plane_store(run_dir) == {
        "run_id": "run_control",
        "valid": True,
        "event_count": 1,
        "job_count": 1,
    }
