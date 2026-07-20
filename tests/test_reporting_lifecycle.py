from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.job_service import create_or_get_pipeline_job, load_pipeline_jobs
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.service import REPORT_SNAPSHOT_JOB_TYPE, ReportRequestService
from autoad_researcher.reporting.snapshot import build_report_snapshot, resolve_run_relative_file
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.worker.main import _process_pending_jobs


def _session(run_dir: Path):
    return ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="tasks/task.json",
        task_hash="a" * 64,
        execution_mode="approve_each_step",
    )[0]


def test_report_request_is_idempotent_and_uses_report_job_identity(tmp_path: Path):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    session = _session(run_dir)
    service = ReportRequestService()

    first, created = service.request(run_dir, session_id=session.session_id)
    second, replayed = service.request(run_dir, session_id=session.session_id)

    assert created is True
    assert replayed is False
    assert first["manifest"].report_id == second["manifest"].report_id
    jobs = load_pipeline_jobs(run_dir)
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == REPORT_SNAPSHOT_JOB_TYPE
    assert jobs[0]["report_id"] == first["manifest"].report_id
    assert jobs[0]["source_id"] == ""


def test_report_request_concurrent_replay_allocates_one_version(tmp_path: Path):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    session = _session(run_dir)
    service = ReportRequestService()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.request(run_dir, session_id=session.session_id)[0], range(2)))

    assert {item["manifest"].report_id for item in results}
    assert len(ReportStore().list_manifests(run_dir, session_id=session.session_id)) == 1
    assert len(load_pipeline_jobs(run_dir)) == 1


def test_snapshot_job_advances_only_to_facts_stage(tmp_path: Path):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    session = _session(run_dir)
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)

    assert _process_pending_jobs(run_dir) == 1

    store = ReportStore()
    state = store.load_state(run_dir, result["manifest"].report_id)
    assert state.generation_status == "assembling_facts"
    assert load_pipeline_jobs(run_dir)[0]["status"] == "completed"


def test_report_job_idempotency_rejects_different_report_owner(tmp_path: Path):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    create_or_get_pipeline_job(
        run_dir,
        source_id="",
        report_id="report_a",
        job_type=REPORT_SNAPSHOT_JOB_TYPE,
        idempotency_key="report:one",
        evidence_role="report_artifact",
    )
    with pytest.raises(ValueError, match="different job identity"):
        create_or_get_pipeline_job(
            run_dir,
            source_id="",
            report_id="report_b",
            job_type=REPORT_SNAPSHOT_JOB_TYPE,
            idempotency_key="report:one",
            evidence_role="report_artifact",
        )


def test_snapshot_resolver_rejects_escape_and_symlink(tmp_path: Path):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (run_dir / "escape.json").symlink_to(outside)

    with pytest.raises(ValueError, match="run-relative"):
        resolve_run_relative_file(run_dir, "../outside.json")
    with pytest.raises(ValueError, match="escapes"):
        resolve_run_relative_file(run_dir, "escape.json")


def test_snapshot_hash_is_stable_for_unchanged_session(tmp_path: Path):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    session = _session(run_dir)

    one = build_report_snapshot(run_dir, session_id=session.session_id)
    two = build_report_snapshot(run_dir, session_id=session.session_id)

    assert one.source_inventory_sha256 == two.source_inventory_sha256
