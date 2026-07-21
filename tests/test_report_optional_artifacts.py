import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.pdf import run_pdf_job
from autoad_researcher.reporting.render_request import request_optional_format
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
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
    for _ in range(5):
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
        assert {
            "report.md",
            "report.html",
            "report_facts.json",
            "evidence_index.json",
            "report_validation.json",
            "report_snapshot.json",
            "delivery_state_snapshot.json",
            "bundle_exclusions.json",
            "checksums.sha256",
        }.issubset(names)
        assert "report_state.json" not in names
        assert "report.pdf" not in names
        assert archive.namelist()[:-1] == sorted(archive.namelist()[:-1])
        for line in archive.read("checksums.sha256").decode("utf-8").splitlines():
            expected, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
        delivery = json.loads(archive.read("delivery_state_snapshot.json"))
        package_job = next(job for job in load_pipeline_jobs(run_dir) if job["job_type"] == "report_package")
        assert delivery["package_job_id"] == package_job["job_id"]
        assert delivery["packaged_at"] == package_job["created_at"]
        exclusions = json.loads(archive.read("bundle_exclusions.json"))
        assert {item["path"] for item in exclusions["excluded"]} >= {"report.pdf", "report_state.json"}
    first_hash = hashlib.sha256(bundle.read_bytes()).hexdigest()
    package_job = next(job for job in load_pipeline_jobs(run_dir) if job["job_type"] == "report_package")
    from autoad_researcher.reporting.bundle import run_bundle_job

    run_bundle_job(run_dir, package_job)
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == first_hash


def test_missing_pdf_capability_marks_only_pdf_failed(tmp_path: Path, monkeypatch):
    run_dir, report_id = _content_ready_report(tmp_path)
    monkeypatch.setattr("autoad_researcher.reporting.pdf.shutil.which", lambda _name: None)
    job, _ = request_optional_format(run_dir, report_id=report_id, format_name="pdf")
    run_pdf_job(run_dir, job)
    state = ReportStore().load_state(run_dir, report_id)
    assert state.generation_status == "content_ready"
    assert state.format_status.pdf == "failed"
    assert (run_dir / "reports" / report_id / "report_pdf_result.json").is_file()


def test_bundle_refuses_to_publish_without_html(tmp_path: Path):
    run_dir, report_id = _content_ready_report(tmp_path)
    directory = run_dir / "reports" / report_id
    (directory / "report.html").unlink()
    ReportStore().set_format_status(run_dir, report_id=report_id, format_name="bundle", status="missing")
    package_job = next(job for job in load_pipeline_jobs(run_dir) if job["job_type"] == "report_package")
    from autoad_researcher.reporting.bundle import run_bundle_job

    with pytest.raises(ValueError, match="requires HTML artifact"):
        run_bundle_job(run_dir, package_job)
