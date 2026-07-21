from pathlib import Path

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeParagraphV1, NarrativeSectionV1, NarrativeSectionsV1
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.reporting.validator import validate_report
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
    assert (directory / "claim_evidence_map.json").is_file()
    state = ReportStore().load_state(run_dir, report_id)
    assert state.generation_status == "content_ready"
    assert state.format_status.markdown == "ready"


def test_validator_rejects_unknown_placeholders_and_unbound_interpretation(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_narrative_validation"
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(
        run_dir, task_ref="tasks/task.json", task_hash="d" * 64, execution_mode="approve_each_step"
    )[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id
    assert _process_pending_jobs(run_dir) == 1
    directory = run_dir / "reports" / report_id
    facts = ExperimentReportFactsV1.model_validate_json((directory / "report_facts.json").read_text(encoding="utf-8"))
    evidence = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
    narrative = NarrativeSectionsV1(
        sections=[
            NarrativeSectionV1(section_id="summary", paragraphs=[NarrativeParagraphV1(paragraph_id="summary", paragraph_kind="background", prose_template="摘要")]),
            NarrativeSectionV1(section_id="interpretation", paragraphs=[NarrativeParagraphV1(paragraph_id="interpretation", paragraph_kind="interpretation", prose_template="{{fact:not_a_fact}}")]),
            NarrativeSectionV1(section_id="limitations", paragraphs=[NarrativeParagraphV1(paragraph_id="limitations", paragraph_kind="limitation", prose_template="限制", claim_ids=["claim_limitations"])]),
            NarrativeSectionV1(section_id="next_steps", paragraphs=[NarrativeParagraphV1(paragraph_id="next", paragraph_kind="recommendation", prose_template="下一步")]),
        ],
        claims=[],
    )

    validation = validate_report(facts=facts, evidence=evidence, narrative=narrative)

    assert not validation.passed
    assert any("unknown Fact placeholder" in error for error in validation.errors)
    assert any("requires a claim ID" in error for error in validation.errors)
