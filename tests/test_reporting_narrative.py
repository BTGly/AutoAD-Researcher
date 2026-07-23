from pathlib import Path

import json

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeParagraphV1, NarrativeSectionV1, NarrativeSectionsV1, StructuredClaimV1
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.reporting.validator import validate_report
from autoad_researcher.reporting.renderer_markdown import render_markdown
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


def test_narrative_agent_uses_only_frozen_context_when_configured(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_reporting_narrative_agent"
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(
        run_dir, task_ref="tasks/task.json", task_hash="e" * 64, execution_mode="approve_each_step"
    )[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id
    assert _process_pending_jobs(run_dir) == 1
    directory = run_dir / "reports" / report_id
    facts = ExperimentReportFactsV1.model_validate_json((directory / "report_facts.json").read_text(encoding="utf-8"))
    evidence = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
    root_evidence = next(item.evidence_id for item in evidence.entries if item.field_path == "$")

    observed = {}
    calls = []

    def fake_call(api_key, provider_url, messages, **kwargs):
        calls.append(messages)
        observed["messages"] = messages
        assert api_key == "test-key"
        assert provider_url == "https://provider.test"
        assert kwargs["response_format_json"] is True
        if len(calls) == 1:
            return {"reply": json.dumps({"schema_version": 2}), "error": ""}
        return {"reply": json.dumps({
            "schema_version": 2,
            "sections": [
                {"section_id": "summary", "paragraphs": [{"paragraph_id": "summary", "paragraph_kind": "background", "prose_template": "冻结摘要", "claim_ids": ["summary_claim"]}]},
                {"section_id": "interpretation", "paragraphs": [{"paragraph_id": "interpretation", "paragraph_kind": "interpretation", "prose_template": "冻结解释", "claim_ids": ["interpretation_claim"]}]},
                {"section_id": "limitations", "paragraphs": [{"paragraph_id": "limitations", "paragraph_kind": "limitation", "prose_template": "冻结限制", "claim_ids": ["limitations_claim"]}]},
                {"section_id": "next_steps", "paragraphs": [{"paragraph_id": "next", "paragraph_kind": "recommendation", "prose_template": "等待确认", "claim_ids": ["next_claim"]}]},
            ],
            "claims": [
                {"claim_id": "summary_claim", "claim_kind": "explanation", "statement_template": "冻结摘要", "fact_refs": [], "evidence_ids": []},
                {"claim_id": "interpretation_claim", "claim_kind": "explanation", "statement_template": "冻结解释", "fact_refs": [], "evidence_ids": []},
                {"claim_id": "limitations_claim", "claim_kind": "limitation", "statement_template": "冻结限制", "fact_refs": ["uncertainties"], "evidence_ids": [root_evidence]},
                {"claim_id": "next_claim", "claim_kind": "recommendation", "statement_template": "等待确认", "fact_refs": [], "evidence_ids": []},
            ],
        }), "error": ""}

    monkeypatch.setenv("AUTOAD_REPORT_API_KEY", "test-key")
    monkeypatch.setenv("AUTOAD_REPORT_BASE_URL", "https://provider.test")
    monkeypatch.setenv("AUTOAD_REPORT_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr("autoad_researcher.reporting.narrative_agent.call_research_chat", fake_call)
    from autoad_researcher.reporting.narrative_agent import generate_narrative

    generated = generate_narrative(facts=facts, evidence=evidence)
    assert generated.mode == "model"
    assert generated.model == "deepseek-v4-flash"
    assert len(calls) == 2
    assert calls[1][2]["role"] == "assistant"
    assert "schema_validation" in calls[1][3]["content"]
    context = observed["messages"][1]["content"]
    assert "test-key" not in context
    assert "uncertainties" in context


def test_selected_model_failure_does_not_publish_a_fallback(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_reporting_model_failure"
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(
        run_dir, task_ref="tasks/task.json", task_hash="1" * 64, execution_mode="approve_each_step"
    )[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id
    assert _process_pending_jobs(run_dir) == 1
    directory = run_dir / "reports" / report_id
    facts = ExperimentReportFactsV1.model_validate_json((directory / "report_facts.json").read_text(encoding="utf-8"))
    evidence = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
    profile = {"mode": "model", "model": "deepseek-v4-flash", "provider_base_url": "https://provider.test", "prompt_sha256": "a" * 64}
    monkeypatch.setenv("AUTOAD_REPORT_API_KEY", "test-key")
    monkeypatch.setattr("autoad_researcher.reporting.narrative_agent.call_research_chat", lambda *_args, **_kwargs: {"error": "down"})
    from autoad_researcher.reporting.narrative_agent import NarrativeGenerationError, generate_narrative

    with pytest.raises(NarrativeGenerationError, match="did not return"):
        generate_narrative(facts=facts, evidence=evidence, profile=profile)


def test_interpretation_renders_claim_template_not_raw_paragraph(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_render_claim"
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(
        run_dir, task_ref="tasks/task.json", task_hash="2" * 64, execution_mode="approve_each_step"
    )[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    assert _process_pending_jobs(run_dir) == 1
    directory = run_dir / "reports" / result["manifest"].report_id
    facts = ExperimentReportFactsV1.model_validate_json((directory / "report_facts.json").read_text(encoding="utf-8"))
    evidence = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
    status_evidence = next(item.evidence_id for item in evidence.entries if "repository_and_environment.status" in item.fact_refs)
    narrative = NarrativeSectionsV1(
        sections=[
            NarrativeSectionV1(section_id="summary", paragraphs=[NarrativeParagraphV1(paragraph_id="summary", paragraph_kind="background", prose_template="摘要")]),
            NarrativeSectionV1(section_id="interpretation", paragraphs=[NarrativeParagraphV1(paragraph_id="interpretation", paragraph_kind="interpretation", prose_template="unpublished paragraph", claim_ids=["claim"])]),
            NarrativeSectionV1(section_id="limitations", paragraphs=[NarrativeParagraphV1(paragraph_id="limitations", paragraph_kind="limitation", prose_template="限制", claim_ids=["claim"])]),
            NarrativeSectionV1(section_id="next_steps", paragraphs=[NarrativeParagraphV1(paragraph_id="next", paragraph_kind="recommendation", prose_template="下一步")]),
        ],
        claims=[StructuredClaimV1(claim_id="claim", claim_kind="explanation", statement_template="已绑定事实：{{fact:repository_and_environment.status}}", fact_refs=["repository_and_environment.status"], evidence_ids=[status_evidence])],
    )
    assert validate_report(facts=facts, evidence=evidence, narrative=narrative).passed
    markdown = render_markdown(facts=facts, narrative=narrative, evidence=evidence)
    assert "unpublished paragraph" not in markdown
    assert "已绑定事实：" in markdown


def test_validator_rejects_improvement_claim_for_non_comparable_attempt(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_noncomparable"
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(
        run_dir, task_ref="tasks/task.json", task_hash="f" * 64, execution_mode="approve_each_step"
    )[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    report_id = result["manifest"].report_id
    assert _process_pending_jobs(run_dir) == 1
    directory = run_dir / "reports" / report_id
    facts = ExperimentReportFactsV1.model_validate_json((directory / "report_facts.json").read_text(encoding="utf-8"))
    evidence = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
    noncomparable = {"attempt_id": "attempt_000001", "outcome": {"evaluation_status": "NON_COMPARABLE", "scientific_effect": "INCONCLUSIVE"}}
    facts = facts.model_copy(update={"attempts": [noncomparable], "non_comparable_attempts": [noncomparable]})
    narrative = NarrativeSectionsV1(
        sections=[
            NarrativeSectionV1(section_id="summary", paragraphs=[NarrativeParagraphV1(paragraph_id="summary", paragraph_kind="background", prose_template="摘要")]),
            NarrativeSectionV1(section_id="interpretation", paragraphs=[NarrativeParagraphV1(paragraph_id="interpretation", paragraph_kind="interpretation", prose_template="解释", claim_ids=["claim"])]),
            NarrativeSectionV1(section_id="limitations", paragraphs=[NarrativeParagraphV1(paragraph_id="limitations", paragraph_kind="limitation", prose_template="限制", claim_ids=["claim"])]),
            NarrativeSectionV1(section_id="next_steps", paragraphs=[NarrativeParagraphV1(paragraph_id="next", paragraph_kind="recommendation", prose_template="下一步")]),
        ],
        claims=[StructuredClaimV1(claim_id="claim", claim_kind="explanation", statement_template="提升", attempt_ids=["attempt_000001"], asserted_scientific_effects={"attempt_000001": "IMPROVEMENT"})],
    )
    validation = validate_report(facts=facts, evidence=evidence, narrative=narrative)
    assert not validation.passed
    assert any("non-comparable" in error for error in validation.errors)


def test_validator_requires_evidence_for_the_same_fact_field(tmp_path: Path):
    run_dir = tmp_path / "run_reporting_fact_evidence"
    run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(
        run_dir, task_ref="tasks/task.json", task_hash="a" * 64, execution_mode="approve_each_step"
    )[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    assert _process_pending_jobs(run_dir) == 1
    directory = run_dir / "reports" / result["manifest"].report_id
    facts = ExperimentReportFactsV1.model_validate_json((directory / "report_facts.json").read_text(encoding="utf-8"))
    evidence = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
    correct = next(item.evidence_id for item in evidence.entries if "repository_and_environment.status" in item.fact_refs)
    wrong = next(item.evidence_id for item in evidence.entries if item.evidence_id != correct)
    narrative = NarrativeSectionsV1(
        sections=[
            NarrativeSectionV1(section_id="summary", paragraphs=[NarrativeParagraphV1(paragraph_id="s", paragraph_kind="background", prose_template="摘要")]),
            NarrativeSectionV1(section_id="interpretation", paragraphs=[NarrativeParagraphV1(paragraph_id="i", paragraph_kind="interpretation", prose_template="解释", claim_ids=["c"])]),
            NarrativeSectionV1(section_id="limitations", paragraphs=[NarrativeParagraphV1(paragraph_id="l", paragraph_kind="limitation", prose_template="限制", claim_ids=["c"])]),
            NarrativeSectionV1(section_id="next_steps", paragraphs=[NarrativeParagraphV1(paragraph_id="n", paragraph_kind="recommendation", prose_template="下一步")]),
        ],
        claims=[StructuredClaimV1(claim_id="c", claim_kind="explanation", statement_template="来源状态", fact_refs=["repository_and_environment.status"], evidence_ids=[wrong])],
    )
    validation = validate_report(facts=facts, evidence=evidence, narrative=narrative)
    assert not validation.passed
    assert any("does not correspond to Fact" in error for error in validation.errors)

    valid = narrative.model_copy(update={"claims": [narrative.claims[0].model_copy(update={"evidence_ids": [correct]})]})
    assert validate_report(facts=facts, evidence=evidence, narrative=valid).passed
