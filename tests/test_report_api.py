from pathlib import Path

import pytest
from fastapi import HTTPException

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.service import ReportRequestService
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
    for _ in range(3):
        _process_pending_jobs(run_dir)
    monkeypatch.setattr(reports, "RUNS_ROOT", str(tmp_path))

    listed = await reports.list_reports(run_id)
    assert listed["reports"][0]["report_id"] == report_id
    assert (await reports.latest_report(run_id))["report_id"] == report_id
    assert (await reports.get_content(run_id, report_id))["format"] == "md"
    evidence_id = (await reports.get_evidence(run_id, report_id, (await reports.get_evidence.__wrapped__ if False else ""))) if False else None
    with pytest.raises(HTTPException) as missing:
        await reports.download_report_artifact(run_id, report_id, "../report.md")
    assert missing.value.status_code == 404
