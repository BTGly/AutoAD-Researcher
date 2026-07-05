"""Tests for Research Chat evidence-based context building."""

from pathlib import Path
from shutil import copytree

from autoad_researcher.assistant.research_context_builder import (
    build_research_chat_evidence_context,
)
from autoad_researcher.ui.sources import append_source_ref, save_uploaded_file, update_source_status


FIXTURE = Path("tests/fixtures/silent_probe_fixture")


def _make_upload(name: str, content: bytes = b"fake pdf content"):
    class Upload:
        pass

    upload = Upload()
    upload.name = name
    upload.getvalue = lambda: content
    return upload


def test_reference_identifier_is_candidate_not_known_fact(tmp_path):
    run_dir = tmp_path / "run_ref"
    run_dir.mkdir()
    append_source_ref(
        run_dir,
        kind="arxiv_id",
        user_label="2303.15140v2",
        stored_path=None,
        status="user_provided_not_ingested",
    )

    context = build_research_chat_evidence_context(run_dir)

    assert context.known_facts == {}
    assert len(context.candidate_references) == 1
    assert context.candidate_references[0].kind == "arxiv_id"
    assert context.candidate_references[0].status == "user_provided_not_ingested"
    assert context.has_parsed_paper_evidence is False


def test_uploaded_not_parsed_pdf_is_not_parsed_paper_evidence(tmp_path):
    run_dir = tmp_path / "run_pdf"
    run_dir.mkdir()
    save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))

    context = build_research_chat_evidence_context(run_dir)

    assert len(context.uploaded_unparsed_sources) == 1
    assert context.uploaded_unparsed_sources[0].status == "uploaded_not_parsed"
    assert context.parsed_paper_evidence == []
    assert context.has_parsed_paper_evidence is False


def test_parsed_paper_artifacts_enter_parsed_evidence(tmp_path):
    run_dir = tmp_path / "run_known"
    copytree(FIXTURE, run_dir)
    info = save_uploaded_file(run_dir, _make_upload("PatchCore.pdf"))
    update_source_status(run_dir, info["source_id"], "parsed")

    context = build_research_chat_evidence_context(run_dir)

    assert context.has_parsed_paper_evidence is True
    assert context.parsed_paper_evidence
    evidence = context.parsed_paper_evidence[0]
    assert "paper/artifacts/paper_summary.json" in evidence.artifact_refs
    assert any("coreset" in method.lower() for method in evidence.paper_methods)


def test_missing_blocking_gaps_are_limited_to_three(tmp_path):
    run_dir = tmp_path / "run_known"
    copytree(FIXTURE, run_dir)

    context = build_research_chat_evidence_context(run_dir)

    assert len(context.missing_blocking_gaps) <= 3
    assert "category" in context.missing_blocking_gaps
    assert "metric_direction" in context.missing_blocking_gaps


def test_malformed_source_registry_does_not_crash_context_builder(tmp_path):
    run_dir = tmp_path / "run_bad_registry"
    (run_dir / "sources").mkdir(parents=True)
    (run_dir / "sources" / "source_references.json").write_text("{not json", encoding="utf-8")

    context = build_research_chat_evidence_context(run_dir)

    assert context.candidate_references == []
    assert context.uploaded_unparsed_sources == []
    assert context.forbidden_assumptions
