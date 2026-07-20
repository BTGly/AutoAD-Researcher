import hashlib
import zipfile
from pathlib import Path

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.pdf import run_pdf_job
from autoad_researcher.reporting.render_request import request_optional_format
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.worker.main import _process_pending_jobs


def _content_ready_report(tmp_path: Path) -> tuple[Path, str]:
    run_dir = tmp_path / "run_report_optional_artifacts"
    run_dir.mkdir()
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="tasks/task.json",
        task_hash="f" * 64,
        execution_mode="approve_each_step",
    )
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    for _ in range(4):
        _process_pending_jobs(run_dir)
    return run_dir, result["manifest"].report_id


def test_bundle_is_finite_and_checksum_verified(tmp_path: Path):
    run_dir, report_id = _content_ready_report(tmp_path)
    state = ReportStore().load_state(run_dir, report_id)
    assert state.generation_status == "content_ready"
    assert state.format_status.bundle == "ready"
    bundle = run_dir / "reports" / report_id / "report_bundle.zip"
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert {"report.md", "report_facts.json", "evidence_index.json", "report_validation.json", "checksums.sha256"}.issubset(names)
        for line in archive.read("checksums.sha256").decode("utf-8").splitlines():
            expected, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected


def test_missing_pdf_capability_marks_only_pdf_failed(tmp_path: Path, monkeypatch):
    run_dir, report_id = _content_ready_report(tmp_path)
    monkeypatch.setattr("autoad_researcher.reporting.pdf.shutil.which", lambda _name: None)
    job, _ = request_optional_format(run_dir, report_id=report_id, format_name="pdf")
    run_pdf_job(run_dir, job)
    state = ReportStore().load_state(run_dir, report_id)
    assert state.generation_status == "content_ready"
    assert state.format_status.pdf == "failed"
    assert (run_dir / "reports" / report_id / "report_pdf_result.json").is_file()
