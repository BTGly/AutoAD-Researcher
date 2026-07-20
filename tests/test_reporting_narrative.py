from pathlib import Path

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.worker.main import _process_pending_jobs


def test_narrative_job_validates_and_publishes_markdown(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_narrative"
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(
        run_dir, task_ref="tasks/task.json", task_hash="c" * 64, execution_mode="approve_each_step"
    )[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id
    assert _process_pending_jobs(run_dir) == 1
    assert _process_pending_jobs(run_dir) == 1
    assert _process_pending_jobs(run_dir) == 1
    directory = run_dir / "reports" / report_id
    assert (directory / "report.md").is_file()
    assert (directory / "report_validation.json").is_file()
    state = ReportStore().load_state(run_dir, report_id)
    assert state.generation_status == "content_ready"
    assert state.format_status.markdown == "ready"
