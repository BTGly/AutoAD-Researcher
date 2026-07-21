from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.job_service import create_or_get_pipeline_job, fail_pipeline_job, load_pipeline_jobs
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.recipe import report_generation_profile, report_recipe_hash
from autoad_researcher.reporting.facts_service import REPORT_FACTS_JOB_TYPE
from autoad_researcher.reporting.service import ReportRequestService, retry_failed_report_job
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
    assert len(jobs) == 5
    assert [item["job_type"] for item in jobs] == [
        REPORT_FACTS_JOB_TYPE,
        "report_narrative_generate",
        "report_validate",
        "report_render_html",
        "report_package",
    ]
    assert jobs[0]["report_id"] == first["manifest"].report_id
    assert jobs[0]["source_id"] == ""
    assert jobs[1]["payload"]["depends_on"] == jobs[0]["job_id"]
    assert jobs[4]["payload"]["depends_on"] == jobs[3]["job_id"]


def test_report_request_concurrent_replay_allocates_one_version(tmp_path: Path):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    session = _session(run_dir)
    service = ReportRequestService()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.request(run_dir, session_id=session.session_id)[0], range(2)))

    assert {item["manifest"].report_id for item in results}
    assert len(ReportStore().list_manifests(run_dir, session_id=session.session_id)) == 1
    assert len(load_pipeline_jobs(run_dir)) == 5


def test_report_recipe_change_allocates_a_new_version(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    session = _session(run_dir)
    first, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    monkeypatch.setattr("autoad_researcher.reporting.service.report_recipe_hash", lambda _profile: "b" * 64)
    second, created = ReportRequestService().request(run_dir, session_id=session.session_id)

    assert created is True
    assert first["manifest"].report_id != second["manifest"].report_id
    assert first["manifest"].report_recipe_hash == report_recipe_hash()
    assert second["manifest"].report_recipe_hash == "b" * 64
    assert len(ReportStore().list_manifests(run_dir, session_id=session.session_id)) == 2


def test_generation_profile_participates_in_report_identity(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_reporting_profile"
    run_dir.mkdir()
    session = _session(run_dir)
    monkeypatch.delenv("AUTOAD_REPORT_API_KEY", raising=False)
    monkeypatch.delenv("AUTOAD_REPORT_BASE_URL", raising=False)
    monkeypatch.delenv("AUTOAD_REPORT_MODEL", raising=False)
    fallback, _ = ReportRequestService().request(run_dir, session_id=session.session_id)

    monkeypatch.setenv("AUTOAD_REPORT_API_KEY", "not-persisted")
    monkeypatch.setenv("AUTOAD_REPORT_BASE_URL", "https://provider.test/")
    monkeypatch.setenv("AUTOAD_REPORT_MODEL", "model-a")
    model, created = ReportRequestService().request(run_dir, session_id=session.session_id)
    replay, replayed = ReportRequestService().request(run_dir, session_id=session.session_id)

    assert created is True and replayed is False
    assert fallback["manifest"].report_id != model["manifest"].report_id == replay["manifest"].report_id
    profile = model["job"]["payload"]["generation_profile"]
    assert profile["mode"] == "model" and profile["model"] == "model-a"
    assert "not-persisted" not in str(profile)


def test_generation_profile_hashes_the_actual_prompt_and_schema(monkeypatch):
    import autoad_researcher.reporting.recipe as recipe

    baseline = report_generation_profile()["prompt_sha256"]
    monkeypatch.setattr(recipe, "narrative_system_prompt", lambda: "changed prompt")

    assert report_generation_profile()["prompt_sha256"] != baseline


def test_recipe_hash_covers_evidence_digest_bundle_and_pdf_versions(monkeypatch):
    import autoad_researcher.reporting.recipe as recipe

    baseline = report_recipe_hash()
    for attribute in (
        "EVIDENCE_INDEX_BUILD_VERSION",
        "REPORT_DIGEST_BUILD_VERSION",
        "REPORT_BUNDLE_FORMAT_VERSION",
        "PDF_RENDERER_VERSION",
    ):
        monkeypatch.setattr(recipe, attribute, f"changed-{attribute}")
        assert report_recipe_hash() != baseline
        monkeypatch.setattr(recipe, attribute, {
            "EVIDENCE_INDEX_BUILD_VERSION": "v2",
            "REPORT_DIGEST_BUILD_VERSION": "v2",
            "REPORT_BUNDLE_FORMAT_VERSION": "v2",
            "PDF_RENDERER_VERSION": "v1",
        }[attribute])


def test_model_narrative_failure_persists_a_retryable_report_job(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_reporting_model_failure"
    run_dir.mkdir()
    session = _session(run_dir)
    monkeypatch.setenv("AUTOAD_REPORT_API_KEY", "test-key")
    monkeypatch.setenv("AUTOAD_REPORT_BASE_URL", "https://provider.test")
    monkeypatch.setenv("AUTOAD_REPORT_MODEL", "test-model")
    monkeypatch.setattr(
        "autoad_researcher.reporting.narrative_agent.call_research_chat",
        lambda *_args, **_kwargs: {"error": "provider unavailable"},
    )
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id

    assert _process_pending_jobs(run_dir) == 1  # facts
    assert _process_pending_jobs(run_dir) == 1  # configured model failure

    narrative_job = next(item for item in load_pipeline_jobs(run_dir) if item["job_type"] == "report_narrative_generate")
    assert narrative_job["status"] == "failed"
    assert ReportStore().load_state(run_dir, report_id).generation_status == "failed"
    assert not (run_dir / "reports" / report_id / "narrative_sections.json").exists()

    retried = retry_failed_report_job(run_dir, report_id=report_id, job_id=narrative_job["job_id"])
    assert retried["status"] == "queued"
    assert retried["retry_count"] == 1
    assert ReportStore().load_state(run_dir, report_id).generation_status == "generating_narrative"


def test_explicit_report_retry_requeues_only_the_failed_job(tmp_path: Path):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    session = _session(run_dir)
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id
    job_id = result["job"]["job_id"]
    ReportStore().mark_failed(run_dir, report_id=report_id, error="fixture failure")
    fail_pipeline_job(run_dir, job_id, error="fixture failure")

    requeued = retry_failed_report_job(run_dir, report_id=report_id, job_id=job_id)

    assert requeued["status"] == "queued"
    assert requeued["retry_count"] == 1
    assert requeued["idempotency_key"] == result["job"]["idempotency_key"]
    assert ReportStore().load_state(run_dir, report_id).generation_status == "assembling_facts"
    with pytest.raises(ValueError, match="only failed"):
        retry_failed_report_job(run_dir, report_id=report_id, job_id=job_id)


def test_validate_retry_returns_only_the_failed_validate_stage_to_its_phase(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_validate_retry"
    run_dir.mkdir()
    session = _session(run_dir)
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id
    _process_pending_jobs(run_dir)
    _process_pending_jobs(run_dir)
    validate_job = next(item for item in load_pipeline_jobs(run_dir) if item["job_type"] == "report_validate")
    ReportStore().mark_failed(run_dir, report_id=report_id, error="fixture validation failure")
    fail_pipeline_job(run_dir, validate_job["job_id"], error="fixture validation failure")

    requeued = retry_failed_report_job(run_dir, report_id=report_id, job_id=validate_job["job_id"])

    assert requeued["job_type"] == "report_validate"
    state = ReportStore().load_state(run_dir, report_id)
    assert state.generation_status == "validating"
    assert state.retry_count == 1


def test_failed_report_stage_blocks_successors_and_retry_resumes_same_graph(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_dependency_retry"
    run_dir.mkdir()
    session = _session(run_dir)
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id
    jobs = load_pipeline_jobs(run_dir)
    facts, narrative = jobs[:2]

    ReportStore().mark_failed(run_dir, report_id=report_id, error="fixture facts failure")
    fail_pipeline_job(run_dir, facts["job_id"], error="fixture facts failure")
    assert _process_pending_jobs(run_dir) == 0
    assert next(item for item in load_pipeline_jobs(run_dir) if item["job_id"] == narrative["job_id"])["status"] == "queued"

    retry_failed_report_job(run_dir, report_id=report_id, job_id=facts["job_id"])
    assert _process_pending_jobs(run_dir) == 1
    assert _process_pending_jobs(run_dir) == 1
    assert next(item for item in load_pipeline_jobs(run_dir) if item["job_id"] == narrative["job_id"])["status"] == "completed"


def test_synchronously_frozen_snapshot_starts_with_facts_job(tmp_path: Path):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    session = _session(run_dir)
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)

    snapshot = ReportStore().load_snapshot(run_dir, result["manifest"].report_id)
    assert snapshot.session_id == session.session_id
    assert _process_pending_jobs(run_dir) == 1

    store = ReportStore()
    state = store.load_state(run_dir, result["manifest"].report_id)
    assert state.generation_status == "generating_narrative"
    assert load_pipeline_jobs(run_dir)[0]["status"] == "completed"


def test_report_job_idempotency_rejects_different_report_owner(tmp_path: Path):
    run_dir = tmp_path / "run_reporting"
    run_dir.mkdir()
    create_or_get_pipeline_job(
        run_dir,
        source_id="",
        report_id="report_a",
        job_type=REPORT_FACTS_JOB_TYPE,
        idempotency_key="report:one",
        evidence_role="report_artifact",
    )
    with pytest.raises(ValueError, match="different job identity"):
        create_or_get_pipeline_job(
            run_dir,
            source_id="",
            report_id="report_b",
            job_type=REPORT_FACTS_JOB_TYPE,
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
