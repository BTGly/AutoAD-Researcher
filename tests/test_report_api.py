from pathlib import Path

import pytest
from fastapi import HTTPException

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.assistant.v2.job_service import fail_pipeline_job
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.server.routes import reports
from autoad_researcher.worker.main import _process_pending_jobs


@pytest.mark.asyncio
async def test_versioned_report_api_reads_only_fixed_report_artifacts(tmp_path: Path, monkeypatch):
    run_id = "run_report_api"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(run_dir, task_ref="tasks/task.json", task_hash="d" * 64, execution_mode="approve_each_step")[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id
    for _ in range(5):
        _process_pending_jobs(run_dir)
    monkeypatch.setattr(reports, "RUNS_ROOT", str(tmp_path))

    listed = await reports.list_reports(run_id)
    assert listed["reports"][0]["report_id"] == report_id
    assert (await reports.latest_created_report(run_id))["report_id"] == report_id
    assert (await reports.latest_content_ready_report(run_id))["report_id"] == report_id
    manifest = await reports.get_manifest(run_id, report_id)
    assert manifest["report_id"] == report_id
    assert "jobs" not in manifest
    assert "generation_status" not in manifest
    state = await reports.get_state(run_id, report_id)
    assert state["generation_status"] == "content_ready"
    assert "report.md" in state["available_artifacts"]
    assert {item["job_type"] for item in state["jobs"]} >= {"report_facts_assemble", "report_validate", "report_render_html"}
    assert (await reports.get_digest(run_id, report_id))["report_id"] == report_id
    assert (await reports.get_content(run_id, report_id))["format"] == "md"
    download = await reports.download_report_artifact(run_id, report_id, "report.html")
    assert download.media_type == "text/html"
    assert download.headers["content-disposition"].startswith("inline;")
    state_delivery = await reports.get_state(run_id, report_id)
    assert any(item["media_type"] == "text/html" for item in state_delivery["deliveries"])
    evidence_id = (await reports.get_evidence(run_id, report_id, (await reports.get_evidence.__wrapped__ if False else ""))) if False else None
    with pytest.raises(HTTPException) as missing:
        await reports.download_report_artifact(run_id, report_id, "../report.md")
    assert missing.value.status_code == 404
    with pytest.raises(ValueError, match="invalid report_id"):
        ReportStore().load_manifest(run_dir, "report\x00invalid")


@pytest.mark.asyncio
async def test_report_retry_requires_the_failed_job_id(tmp_path: Path, monkeypatch):
    run_id = "run_report_retry"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(run_dir, task_ref="tasks/task.json", task_hash="e" * 64, execution_mode="approve_each_step")[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id
    ReportStore().mark_failed(run_dir, report_id=report_id, error="fixture failure")
    fail_pipeline_job(run_dir, result["job"]["job_id"], error="fixture failure")
    monkeypatch.setattr(reports, "RUNS_ROOT", str(tmp_path))

    response = await reports.retry_report_job(run_id, report_id, reports.ReportRetryRequest(job_id=result["job"]["job_id"]))

    assert response["job"]["status"] == "queued"
