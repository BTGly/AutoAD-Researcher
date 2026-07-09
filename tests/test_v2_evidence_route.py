from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence, load_usable_evidence
from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.job_service import append_pipeline_job, fail_pipeline_job, load_pipeline_jobs
from autoad_researcher.assistant.v2.reply_planner import plan_reply
from autoad_researcher.paper_intelligence.reading_artifacts import build_paper_reading_artifacts
from autoad_researcher.server.worker_runtime import embedded_worker_enabled
from autoad_researcher.server.routes import draft as draft_route
from autoad_researcher.server.routes import evidence as evidence_route
from autoad_researcher.server.routes import sources as sources_route
from autoad_researcher.tools.markitdown_adapter import convert_local_to_markdown
from autoad_researcher.tools.pdf_text_adapter import convert_pdf_to_markdown
from autoad_researcher.tools.providers import GitHubCommitRef, GitHubRepositoryMetadata
from autoad_researcher.ui.sources import append_source_ref
from autoad_researcher.worker.main import (
    _process_pending_jobs,
    _run_git_clone,
    _run_archive_unpack_classify,
    _run_local_repo_unpack,
    _run_paper_fallbacks,
    _run_paper_parse_pdftotext,
    _run_paper_summarize,
    _run_repo_analyze,
)


def test_append_artifact_evidence_is_loaded_as_usable(tmp_path: Path):
    run_dir = tmp_path / "run_demo"
    run_dir.mkdir()

    append_artifact_evidence(
        run_dir,
        source_id="src_web",
        artifact_path="sources/src_web/content.md",
        evidence_type="web_markdown",
        parser_name="markitdown",
        summary="Converted web page text",
    )

    loaded = load_usable_evidence(run_dir)

    assert loaded[0]["source_id"] == "src_web"
    assert loaded[0]["artifact_path"] == "sources/src_web/content.md"
    assert loaded[0]["support_level"] == "supported"
    assert loaded[0]["summary"] == "Converted web page text"


def test_local_repo_unpack_then_repo_summary_creates_evidence(tmp_path: Path):
    run_dir = tmp_path / "run_demo"
    source_dir = run_dir / "sources" / "src_repo"
    source_dir.mkdir(parents=True)
    archive_path = source_dir / "repo.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("patchcore/README.md", "# PatchCore\nbaseline repository\n")
        zf.writestr("patchcore/patchcore/__init__.py", "")
    append_source_ref(
        run_dir,
        kind="local_repo",
        user_label="repo.zip",
        stored_path="sources/src_repo/repo.zip",
        status="uploaded_not_parsed",
        source_id="src_repo",
    )

    ok, outputs = _run_local_repo_unpack(
        run_dir,
        {
            "job_id": "job_000001",
            "source_id": "src_repo",
            "job_type": "local_repo_unpack",
            "payload": {"stored_path": "sources/src_repo/repo.zip"},
        },
    )

    assert ok is True
    assert "repos/src_repo" in outputs
    assert (run_dir / "repos" / "src_repo" / "README.md").is_file()
    assert (run_dir / "repo_acquisition" / "src_repo" / "repository_attestation.json").is_file()

    summary_ok, summary_outputs = _run_repo_analyze(
        run_dir,
        {"job_id": "job_000002", "source_id": "src_repo", "job_type": "repo_summarize"},
    )

    assert summary_ok is True
    assert summary_outputs == ["repos/src_repo/repo_brief.md"]
    evidence = load_usable_evidence(run_dir)
    assert evidence[0]["evidence_type"] == "repo_summary"
    assert "PatchCore" in evidence[0]["summary"]


def test_archive_bundle_classifies_mixed_materials_and_queues_child_jobs(tmp_path: Path):
    run_dir = tmp_path / "run_demo"
    source_dir = run_dir / "sources" / "src_bundle"
    source_dir.mkdir(parents=True)
    archive_path = source_dir / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("repo/README.md", "# Repo\n")
        zf.writestr("repo/pyproject.toml", "[project]\nname='demo'\n")
        zf.writestr("repo/demo/__init__.py", "")
        zf.writestr("paper/2303.15140v2.pdf", b"%PDF fake")
        zf.writestr("docs/notes.md", "# Notes\n")
        zf.writestr("docs/revision.docx", b"docx bytes")
    append_source_ref(
        run_dir,
        kind="archive_bundle",
        user_label="bundle.zip",
        stored_path="sources/src_bundle/bundle.zip",
        status="uploaded_not_parsed",
        source_id="src_bundle",
    )

    ok, outputs = _run_archive_unpack_classify(
        run_dir,
        {
            "job_id": "job_000001",
            "source_id": "src_bundle",
            "job_type": "archive_unpack_classify",
            "payload": {"stored_path": "sources/src_bundle/bundle.zip"},
        },
    )

    assert ok is True
    assert outputs == ["archive_unpack/src_bundle/archive_manifest.json"]
    registry = json.loads((run_dir / "sources" / "source_references.json").read_text(encoding="utf-8"))
    children = [source for source in registry["sources"] if source.get("parent_source_id") == "src_bundle"]
    assert {child["kind"] for child in children} == {"local_repo", "paper_pdf", "markdown", "document"}
    jobs = load_pipeline_jobs(run_dir)
    assert "local_repo_acquire" in [job["job_type"] for job in jobs]
    assert "repo_summarize" in [job["job_type"] for job in jobs]
    assert "paper_parse_mineru" in [job["job_type"] for job in jobs]
    assert "document_markitdown" in [job["job_type"] for job in jobs]
    evidence = load_usable_evidence(run_dir)
    assert any(item["evidence_type"] == "archive_manifest" for item in evidence)
    assert any(item["evidence_type"] == "uploaded_text" for item in evidence)


@pytest.mark.asyncio
async def test_evidence_route_returns_v2_evidence(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(evidence_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_demo"
    run_dir.mkdir()
    attestation_path = run_dir / "repo_acquisition" / "src_repo" / "repository_attestation.json"
    attestation_path.parent.mkdir(parents=True)
    attestation_path.write_text("{}", encoding="utf-8")
    append_artifact_evidence(
        run_dir,
        source_id="src_repo",
        artifact_path="repos/src_repo/repo_brief.md",
        evidence_type="repo_summary",
        parser_name="repo_summarizer",
        summary="Repository summary text",
    )

    payload = await evidence_route.get_evidence("run_demo")

    assert len(payload) == 1
    assert payload[0]["evidence_type"] == "repo_summary"
    assert payload[0]["support_level"] == "supported"


@pytest.mark.asyncio
async def test_evidence_route_rejects_path_traversal_run_id(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(evidence_route, "RUNS_ROOT", str(tmp_path))

    with pytest.raises(HTTPException) as excinfo:
        await evidence_route.get_evidence("../outside")

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_source_removes_registry_evidence_and_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sources_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_demo"
    source_dir = run_dir / "sources" / "src_wrong"
    source_dir.mkdir(parents=True)
    (source_dir / "wrong.md").write_text("wrong material", encoding="utf-8")
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_wrong",
                    "kind": "markdown",
                    "user_label": "wrong.md",
                    "status": "uploaded_not_parsed",
                    "stored_path": "sources/src_wrong/wrong.md",
                }
            ],
        }),
        encoding="utf-8",
    )
    append_artifact_evidence(
        run_dir,
        source_id="src_wrong",
        artifact_path="sources/src_wrong/wrong.md",
        evidence_type="uploaded_text",
        parser_name="direct_upload",
        summary="wrong material",
    )

    deleted = await sources_route.delete_source("run_demo", "src_wrong")

    assert deleted == {"source_id": "src_wrong", "deleted": True, "removed_evidence": 1}
    assert json.loads((run_dir / "sources" / "source_references.json").read_text(encoding="utf-8"))["sources"] == []
    assert not source_dir.exists()
    assert load_usable_evidence(run_dir) == []


@pytest.mark.asyncio
async def test_delete_archive_bundle_removes_child_sources_and_evidence(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sources_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_demo"
    (run_dir / "sources" / "src_bundle").mkdir(parents=True)
    (run_dir / "sources" / "src_child").mkdir(parents=True)
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_bundle",
                    "kind": "archive_bundle",
                    "user_label": "bundle.zip",
                    "status": "parsed",
                    "stored_path": "sources/src_bundle/bundle.zip",
                },
                {
                    "source_id": "src_child",
                    "kind": "markdown",
                    "parent_source_id": "src_bundle",
                    "user_label": "notes.md",
                    "status": "parsed",
                    "stored_path": "sources/src_child/notes.md",
                },
            ],
        }),
        encoding="utf-8",
    )
    append_artifact_evidence(
        run_dir,
        source_id="src_child",
        artifact_path="sources/src_child/notes.md",
        evidence_type="uploaded_text",
        parser_name="archive_bundle",
        summary="child notes",
    )

    deleted = await sources_route.delete_source("run_demo", "src_bundle")

    assert deleted == {"source_id": "src_bundle", "deleted": True, "removed_evidence": 1}
    assert json.loads((run_dir / "sources" / "source_references.json").read_text(encoding="utf-8"))["sources"] == []
    assert not (run_dir / "sources" / "src_bundle").exists()
    assert not (run_dir / "sources" / "src_child").exists()
    assert load_usable_evidence(run_dir) == []


@pytest.mark.asyncio
async def test_draft_route_returns_chinese_missing_state(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(draft_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_demo"
    (run_dir / "chat").mkdir(parents=True)
    (run_dir / "chat" / "transcript.jsonl").write_text(
        json.dumps({"role": "user", "content": "pathcore为基线，然后指标AUROC，数据集mvtec"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    payload = await draft_route.get_draft("run_demo")

    assert payload["title"] == "研究计划草案"
    assert payload["has_draft"] is True
    fields = {item["field"]: item for item in payload["fields"]}
    assert fields["baseline"]["value"] == "PatchCore"
    assert fields["dataset"]["value"] == "MVTec AD"
    assert "AUROC" in fields["primary_metrics"]["value"]
    assert any(item["label"] == "成功标准" for item in payload["missing"])


@pytest.mark.asyncio
async def test_draft_route_deduplicates_method_hints(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(draft_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_demo"
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "evidence_index.jsonl").write_text(
        "\n".join([
            json.dumps({
                "source_id": "src_pdf",
                "support_level": "supported",
                "evidence_type": "paper_markdown_fallback",
                "artifact_path": "paper.md",
                "summary": "SimpleNet feature adaptor",
            }),
            json.dumps({
                "source_id": "src_pdf",
                "support_level": "supported",
                "evidence_type": "paper_reading_summary",
                "artifact_path": "summary.md",
                "summary": "SimpleNet discriminator",
            }),
        ])
        + "\n",
        encoding="utf-8",
    )

    payload = await draft_route.get_draft("run_demo")

    fields = {item["field"]: item for item in payload["fields"]}
    assert fields["preferred_method_hints"]["value"] == "SimpleNet 论文方法"


@pytest.mark.asyncio
async def test_evidence_state_route_reports_unusable_sources(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(evidence_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_demo"
    qr = run_dir / "paper" / "parse" / "attempts" / "pa_000001" / "parse_quality_report.json"
    qr.parent.mkdir(parents=True)
    qr.write_text('{"quality_level":"unusable"}', encoding="utf-8")
    (run_dir / "sources").mkdir()
    (run_dir / "sources" / "source_references.json").write_text(
        '{"schema_version":1,"sources":[{"source_id":"src_pdf","kind":"paper_pdf","user_label":"paper.pdf","status":"failed","active_parse_attempt_id":"pa_000001","parse_attempts":[{"parse_attempt_id":"pa_000001","parser":"mineru_pipeline_v1","status":"failed","quality_report":"paper/parse/attempts/pa_000001/parse_quality_report.json","warnings":["no readable paper.md"]}]}]}',
        encoding="utf-8",
    )

    payload = await evidence_route.get_evidence_state("run_demo")

    assert payload["usable_evidence"] == []
    assert payload["unusable_parsed_sources"][0]["source_id"] == "src_pdf"


def test_markitdown_adapter_missing_module_uses_builtin_html_fallback(tmp_path: Path, monkeypatch):
    import builtins

    input_path = tmp_path / "raw.html"
    input_path.write_text("<html><body>hello</body></html>", encoding="utf-8")
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "markitdown":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = convert_local_to_markdown(input_path, tmp_path / "content.md", run_dir=tmp_path)

    assert result.ok is True
    assert result.parser_name == "builtin_text"
    assert result.output_paths == ["content.md"]
    assert "hello" in (tmp_path / "content.md").read_text(encoding="utf-8")


def test_markitdown_builtin_fallback_refuses_binary_files(tmp_path: Path, monkeypatch):
    import builtins

    input_path = tmp_path / "raw.docx"
    input_path.write_bytes(b"PK\x03\x04\x00\x00\x00\x00binary-docx")
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "markitdown":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = convert_local_to_markdown(input_path, tmp_path / "content.md", run_dir=tmp_path)

    assert result.ok is False
    assert "builtin fallback does not support .docx files" in str(result.error)
    assert not (tmp_path / "content.md").exists()


def test_pdftotext_adapter_extracts_real_uploaded_pdf(tmp_path: Path):
    source = Path("runs/run_20260708_1102_1909/sources/src_2026-07-08T11-02-39-541806Z/2303.15140v2.pdf")
    if not source.is_file():
        pytest.skip("local uploaded PDF fixture is not present")

    result = convert_pdf_to_markdown(source, tmp_path / "paper.md", run_dir=tmp_path)

    assert result.ok is True
    text = (tmp_path / "paper.md").read_text(encoding="utf-8")
    assert "SimpleNet" in text
    assert "anomaly detection" in text.lower()


def test_worker_paper_fallbacks_create_usable_evidence_from_pdf(tmp_path: Path):
    fixture = Path("runs/run_20260708_1102_1909/sources/src_2026-07-08T11-02-39-541806Z/2303.15140v2.pdf")
    if not fixture.is_file():
        pytest.skip("local uploaded PDF fixture is not present")
    run_dir = tmp_path / "run_pdf_fallback"
    source_dir = run_dir / "sources" / "src_pdf"
    source_dir.mkdir(parents=True)
    target_pdf = source_dir / "paper.pdf"
    target_pdf.write_bytes(fixture.read_bytes())
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_pdf",
                    "kind": "paper_pdf",
                    "user_label": "2303.15140v2.pdf",
                    "status": "failed",
                    "stored_path": "sources/src_pdf/paper.pdf",
                    "active_parse_attempt_id": None,
                    "parse_attempts": [],
                }
            ],
        }),
        encoding="utf-8",
    )

    ok, outputs = _run_paper_fallbacks(run_dir, {"source_id": "src_pdf", "payload": {}})

    assert ok is True
    assert "paper/parse/pdftotext/src_pdf/paper.md" in outputs
    context = build_llm_context(run_dir)
    assert context["answerability"]["can_answer"] is True
    assert any(item["parser_name"] == "pdftotext" for item in context["usable_evidence"])


class _BodyRequest:
    def __init__(self, content: bytes):
        self._content = content

    async def body(self) -> bytes:
        return self._content


@pytest.mark.asyncio
async def test_upload_pdf_registers_source_and_parse_job(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sources_route, "RUNS_ROOT", str(tmp_path))

    payload = await sources_route.upload_source(
        "run_upload",
        _BodyRequest(b"%PDF fake"),
        x_autoad_filename="2303.15140v2.pdf",
    )

    assert payload["source"]["kind"] == "paper_pdf"
    assert payload["source"]["stored_path"].endswith("/2303.15140v2.pdf")
    assert payload["jobs"][0]["job_type"] == "paper_parse_mineru"
    assert payload["jobs"][0]["source_id"] == payload["source"]["source_id"]
    assert (tmp_path / "run_upload" / payload["source"]["stored_path"]).is_file()


@pytest.mark.asyncio
async def test_upload_source_rejects_path_traversal_run_id(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sources_route, "RUNS_ROOT", str(tmp_path))

    with pytest.raises(HTTPException) as excinfo:
        await sources_route.upload_source(
            "../outside",
            _BodyRequest(b"# Notes\nUseful text."),
            x_autoad_filename="notes.md",
        )

    assert excinfo.value.status_code == 400
    assert not (tmp_path.parent / "outside").exists()


@pytest.mark.asyncio
async def test_upload_markdown_creates_text_evidence(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sources_route, "RUNS_ROOT", str(tmp_path))

    payload = await sources_route.upload_source(
        "run_upload",
        _BodyRequest(b"# Notes\nUseful text."),
        x_autoad_filename="notes.md",
    )

    assert payload["source"]["kind"] == "markdown"
    assert payload["jobs"] == []
    assert payload["artifacts"] == [payload["source"]["stored_path"]]
    evidence = load_usable_evidence(tmp_path / "run_upload")
    assert evidence[0]["evidence_type"] == "uploaded_text"
    assert evidence[0]["artifact_path"] == payload["source"]["stored_path"]


@pytest.mark.asyncio
async def test_upload_archive_bundle_queues_classification(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sources_route, "RUNS_ROOT", str(tmp_path))

    archive = tmp_path / "repo.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("patchcore/README.md", "# PatchCore\n")

    payload = await sources_route.upload_source(
        "run_upload",
        _BodyRequest(archive.read_bytes()),
        x_autoad_filename="repo.zip",
    )

    assert payload["source"]["kind"] == "archive_bundle"
    assert [job["job_type"] for job in payload["jobs"]] == ["archive_unpack_classify"]
    assert payload["jobs"][0]["payload"]["stored_path"] == payload["source"]["stored_path"]


def test_llm_context_includes_queued_pipeline_jobs(tmp_path: Path):
    run_dir = tmp_path / "run_jobs"
    run_dir.mkdir()
    append_pipeline_job(
        run_dir,
        source_id="src_pdf",
        job_type="paper_parse_mineru",
        evidence_role="parsed_paper_evidence",
    )

    context = build_llm_context(run_dir)

    assert context["pending_jobs"][0]["job_type"] == "paper_parse_mineru"
    assert context["pending_jobs"][0]["status"] == "queued"
    assert "job(s) queued or running" in context["answerability"]["limitations"][0] or context["pending_jobs"]


def test_missing_paper_markdown_is_not_supported_evidence(tmp_path: Path):
    run_dir = tmp_path / "run_bad_parse"
    attempt_dir = run_dir / "paper" / "parse" / "attempts" / "pa_000001"
    attempt_dir.mkdir(parents=True)
    (run_dir / "paper").mkdir(exist_ok=True)
    (run_dir / "paper" / "evidence_index.jsonl").write_text(
        '{"schema_version":1,"parse_attempt_id":"pa_000001","evidence":{"evidence_id":"ev_src_pdf_001","source_id":"src_pdf","parse_attempt_id":"pa_000001","physical_page_index":0,"block_id":"b_0_0"}}\n',
        encoding="utf-8",
    )

    evidence = load_usable_evidence(run_dir)

    assert not any(item.get("evidence_type") == "paper_text" for item in evidence)


def test_context_reports_unusable_parsed_source(tmp_path: Path):
    run_dir = tmp_path / "run_unusable"
    qr = run_dir / "paper" / "parse" / "attempts" / "pa_000001" / "parse_quality_report.json"
    qr.parent.mkdir(parents=True)
    qr.write_text(
        '{"schema_version":1,"status":"success","parse_attempt_id":"pa_000001","source_id":"src_pdf","parser":"mineru_pipeline_v1","quality_level":"unusable","usable_for":[],"not_usable_for":["supported_research_facts"],"page_count":1}',
        encoding="utf-8",
    )
    (run_dir / "sources").mkdir()
    (run_dir / "sources" / "source_references.json").write_text(
        '{"schema_version":1,"sources":[{"source_id":"src_pdf","kind":"paper_pdf","user_label":"paper.pdf","status":"failed","stored_path":"sources/src_pdf/paper.pdf","active_parse_attempt_id":"pa_000001","parse_attempts":[{"parse_attempt_id":"pa_000001","parser":"mineru_pipeline_v1","status":"failed","quality_report":"paper/parse/attempts/pa_000001/parse_quality_report.json","warnings":["parse produced no readable paper.md"]}]}]}',
        encoding="utf-8",
    )

    context = build_llm_context(run_dir)

    assert context["answerability"]["blocking_next_step"] == "parse_quality"
    assert context["unusable_parsed_sources"][0]["source_id"] == "src_pdf"


def test_context_reports_failed_attempt_without_active_parse_attempt(tmp_path: Path):
    run_dir = tmp_path / "run_unusable_no_active"
    qr = run_dir / "paper" / "parse" / "attempts" / "pa_000001" / "parse_quality_report.json"
    qr.parent.mkdir(parents=True)
    qr.write_text(
        json.dumps({
            "schema_version": 1,
            "status": "success",
            "parse_attempt_id": "pa_000001",
            "source_id": "src_pdf",
            "parser": "mineru_pipeline_v1",
            "quality_level": "unusable",
            "usable_for": [],
            "not_usable_for": ["supported_research_facts"],
            "fatal_errors": ["parse produced no readable paper.md"],
        }),
        encoding="utf-8",
    )
    source_error_dir = run_dir / "sources" / "src_pdf"
    source_error_dir.mkdir(parents=True)
    (source_error_dir / "markitdown_error.json").write_text(
        json.dumps({
            "source_id": "src_pdf",
            "parser_name": "markitdown",
            "error": "markitdown unavailable: No module named 'markitdown'",
        }),
        encoding="utf-8",
    )
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_pdf",
                    "kind": "paper_pdf",
                    "user_label": "paper.pdf",
                    "status": "failed",
                    "stored_path": "sources/src_pdf/paper.pdf",
                    "active_parse_attempt_id": None,
                    "parse_attempts": [
                        {
                            "parse_attempt_id": "pa_000001",
                            "parser": "mineru_pipeline_v1",
                            "status": "failed",
                            "quality_report": "paper/parse/attempts/pa_000001/parse_quality_report.json",
                            "warnings": ["parse produced no readable paper.md; parsed text is not usable evidence"],
                        }
                    ],
                }
            ],
        }),
        encoding="utf-8",
    )

    context = build_llm_context(run_dir)

    assert context["answerability"]["blocking_next_step"] == "parse_quality"
    unusable = context["unusable_parsed_sources"][0]
    assert unusable["parse_attempt_id"] == "pa_000001"
    assert unusable["fatal_errors"] == ["parse produced no readable paper.md"]
    assert unusable["parser_errors"][0]["error"] == "markitdown unavailable: No module named 'markitdown'"


def test_supported_pdf_fallback_suppresses_unusable_source_state(tmp_path: Path):
    run_dir = tmp_path / "run_pdf_fallback"
    qr = run_dir / "paper" / "parse" / "attempts" / "pa_000001" / "parse_quality_report.json"
    qr.parent.mkdir(parents=True)
    qr.write_text(
        json.dumps({
            "schema_version": 1,
            "status": "success",
            "parse_attempt_id": "pa_000001",
            "source_id": "src_pdf",
            "parser": "mineru_pipeline_v1",
            "quality_level": "unusable",
            "usable_for": [],
            "not_usable_for": ["supported_research_facts"],
            "fatal_errors": ["parse produced no readable paper.md"],
        }),
        encoding="utf-8",
    )
    (run_dir / "sources").mkdir()
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_pdf",
                    "kind": "paper_pdf",
                    "user_label": "paper.pdf",
                    "status": "failed",
                    "stored_path": "sources/src_pdf/paper.pdf",
                    "active_parse_attempt_id": None,
                    "parse_attempts": [
                        {
                            "parse_attempt_id": "pa_000001",
                            "parser": "mineru_pipeline_v1",
                            "status": "failed",
                            "quality_report": "paper/parse/attempts/pa_000001/parse_quality_report.json",
                            "warnings": ["parse produced no readable paper.md"],
                        }
                    ],
                }
            ],
        }),
        encoding="utf-8",
    )
    fallback_md = run_dir / "paper" / "parse" / "pdftotext" / "src_pdf" / "paper.md"
    fallback_md.parent.mkdir(parents=True)
    fallback_md.write_text("# Readable fallback\n\nSimpleNet content.", encoding="utf-8")
    append_artifact_evidence(
        run_dir,
        source_id="src_pdf",
        artifact_path="paper/parse/pdftotext/src_pdf/paper.md",
        evidence_type="paper_markdown_fallback",
        parser_name="pdftotext",
        summary="Readable fallback text",
    )

    context = build_llm_context(run_dir)

    assert context["answerability"]["can_answer"] is True
    assert context["unusable_parsed_sources"] == []


def test_paper_reading_artifacts_are_supported_evidence_and_context(tmp_path: Path):
    run_dir = tmp_path / "run_paper"
    paper_md = run_dir / "paper" / "parse" / "attempts" / "pa_000001" / "paper.md"
    paper_md.parent.mkdir(parents=True)
    paper_md.write_text(
        "# PatchCore Improvement\n\n"
        "## Method\n\n"
        "We propose an anomaly detection approach using patch features and coreset sampling for MVTec AD experiments.\n\n"
        "## Experiments\n\n"
        "The method is evaluated on MVTec bottle with AUROC, runtime, and memory measurements.\n",
        encoding="utf-8",
    )
    artifacts = build_paper_reading_artifacts(
        run_dir,
        source_id="src_pdf",
        parse_attempt_id="pa_000001",
        paper_markdown_relpath="paper/parse/attempts/pa_000001/paper.md",
        parser_name="mineru_pipeline_v1",
    )
    assert artifacts is not None
    append_artifact_evidence(
        run_dir,
        source_id="src_pdf",
        artifact_path=artifacts.summary_md_path,
        evidence_type="paper_reading_summary",
        parser_name="paper_reading_summarizer",
        summary=artifacts.summary,
        raw={"source_markdown": "paper/parse/attempts/pa_000001/paper.md", "anchors": artifacts.anchors},
    )

    evidence = load_usable_evidence(run_dir)
    context = build_llm_context(run_dir)

    assert any(item["evidence_type"] == "paper_reading_summary" for item in evidence)
    assert context["answerability"]["can_answer"] is True
    assert context["paper_reading_summaries"][0]["raw"]["source_markdown"].endswith("paper.md")


def test_worker_paper_summarize_job_writes_manifest_and_evidence(tmp_path: Path):
    run_dir = tmp_path / "run_worker"
    paper_md = run_dir / "paper" / "parse" / "attempts" / "pa_000001" / "paper.md"
    paper_md.parent.mkdir(parents=True)
    paper_md.write_text(
        "# Paper\n\n## Method\n\nThis anomaly detection method uses feature memory and nearest neighbor scoring on MVTec data.\n",
        encoding="utf-8",
    )
    (run_dir / "sources").mkdir()
    (run_dir / "sources" / "source_references.json").write_text(
        '{"schema_version":1,"sources":[{"source_id":"src_pdf","kind":"paper_pdf","user_label":"paper.pdf","status":"parsed","active_parse_attempt_id":"pa_000001","parse_attempts":[{"parse_attempt_id":"pa_000001","parser":"mineru_pipeline_v1","status":"ok","quality_report":"paper/parse/attempts/pa_000001/parse_quality_report.json","warnings":[]}]}]}',
        encoding="utf-8",
    )

    ok, outputs = _run_paper_summarize(run_dir, {"source_id": "src_pdf", "payload": {}})

    assert ok is True
    assert "paper/artifacts/paper_reading_summary.md" in outputs
    assert "paper/artifacts/paper_artifact_manifest.json" in outputs
    evidence = load_usable_evidence(run_dir)
    assert any(item["evidence_type"] == "paper_artifact_manifest" for item in evidence)


def test_reply_fallback_mentions_pending_jobs():
    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "parse"},
            "unparsed_sources": ["src_pdf"],
            "usable_evidence": [],
            "readable_summaries": [],
            "pending_jobs": [{"job_id": "job_000001", "job_type": "paper_parse_mineru", "status": "queued"}],
            "failed_jobs": [],
        },
        "pdf什么时候解析完成",
    )

    assert "paper_parse_mineru" in reply
    assert "queued" in reply
    assert "不能声称已经读完 PDF" in reply


def test_reply_fallback_mentions_unusable_parse():
    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "parse_quality"},
            "unparsed_sources": [],
            "usable_evidence": [],
            "readable_summaries": [],
            "pending_jobs": [],
            "failed_jobs": [],
            "unusable_parsed_sources": [{"source_id": "src_pdf", "user_label": "paper.pdf"}],
        },
        "你看不到解析结果吗",
    )

    assert "解析不可用 source" in reply
    assert "不能从中提取论文方法" in reply


def test_embedded_worker_enabled_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AUTOAD_EMBEDDED_WORKER", "0")

    assert embedded_worker_enabled() is False


def test_worker_git_clone_uses_repository_acquisition_runner(tmp_path: Path, monkeypatch):
    class FakeGitHubReadProvider:
        def repository_metadata(self, owner: str, repository: str) -> GitHubRepositoryMetadata:
            return GitHubRepositoryMetadata(
                owner=owner,
                repository=repository,
                default_branch="main",
                is_fork=False,
                is_archived=False,
                html_url=f"https://github.com/{owner}/{repository}",
            )

        def commit_ref(self, owner: str, repository: str, ref: str) -> GitHubCommitRef:
            return GitHubCommitRef(owner=owner, repository=repository, ref=ref, sha="a" * 40)

    class FakeAcquisitionResult:
        status = "success"
        error_message = None

    class FakeRepositoryAcquisitionRunner:
        def __init__(self, timeout_seconds: int):
            assert timeout_seconds == 120

        def acquire(self, request, *, run_dir: Path):
            assert request.source_id == "src_repo"
            assert request.workspace_root == run_dir.parent.parent
            assert request.remote_url == "https://github.com/example/repo"
            assert request.resolved_ref == "main"
            assert request.resolved_commit == "a" * 40
            assert request.acquisition_profile == "shallow_ref"
            (request.workspace_root / "repos" / request.source_id).mkdir(parents=True)
            (request.workspace_root / "repos" / request.source_id / "README.md").write_text("# Repo\n", encoding="utf-8")
            run_dir.mkdir(parents=True)
            (run_dir / "repository_source.json").write_text("{}", encoding="utf-8")
            (run_dir / "repository_attestation.json").write_text("{}", encoding="utf-8")
            (run_dir / "evidence_index.jsonl").write_text("", encoding="utf-8")
            return FakeAcquisitionResult()

    import autoad_researcher.tools.providers as providers
    import autoad_researcher.repository_intelligence.acquisition as acquisition

    monkeypatch.setattr(providers, "GitHubReadProvider", FakeGitHubReadProvider)
    monkeypatch.setattr(acquisition, "RepositoryAcquisitionRunner", FakeRepositoryAcquisitionRunner)

    run_dir = tmp_path / "run_repo"
    (run_dir / "sources").mkdir(parents=True)
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_repo",
                    "kind": "github_repo",
                    "user_label": "https://github.com/example/repo",
                    "status": "user_provided_not_ingested",
                }
            ],
        }),
        encoding="utf-8",
    )

    ok, outputs = _run_git_clone(run_dir, {"source_id": "src_repo"})

    assert ok is True
    assert "repos/src_repo" in outputs
    assert (run_dir / "repos" / "src_repo" / "README.md").is_file()
    assert (run_dir / "repo_acquisition" / "src_repo" / "repository_source.json").is_file()
    assert (run_dir / "repo_acquisition" / "src_repo" / "repository_attestation.json").is_file()


def test_worker_git_clone_uses_generic_shallow_for_gitlab_url(tmp_path: Path, monkeypatch):
    class FakeRepositoryAcquisitionRunner:
        def __init__(self, timeout_seconds: int):
            assert timeout_seconds == 120

        def acquire(self, request, *, run_dir: Path):
            assert request.source_id == "src_gitlab"
            assert request.remote_url == "https://gitlab.com/example-group/example-repo"
            assert request.resolved_ref is None
            assert request.resolved_commit is None
            assert request.acquisition_profile == "generic_shallow"
            (request.workspace_root / "repos" / request.source_id).mkdir(parents=True)
            (request.workspace_root / "repos" / request.source_id / "README.md").write_text("# Repo\n", encoding="utf-8")
            run_dir.mkdir(parents=True)
            (run_dir / "repository_source.json").write_text("{}", encoding="utf-8")
            (run_dir / "repository_attestation.json").write_text("{}", encoding="utf-8")
            (run_dir / "evidence_index.jsonl").write_text("", encoding="utf-8")
            return type("FakeAcquisitionResult", (), {"status": "success", "error_message": None})()

    import autoad_researcher.repository_intelligence.acquisition as acquisition

    monkeypatch.setattr(acquisition, "RepositoryAcquisitionRunner", FakeRepositoryAcquisitionRunner)

    run_dir = tmp_path / "run_gitlab"
    (run_dir / "sources").mkdir(parents=True)
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_gitlab",
                    "kind": "github_repo",
                    "user_label": "https://gitlab.com/example-group/example-repo",
                    "status": "user_provided_not_ingested",
                }
            ],
        }),
        encoding="utf-8",
    )

    ok, outputs = _run_git_clone(run_dir, {"source_id": "src_gitlab"})

    assert ok is True
    assert "repos/src_gitlab" in outputs


def test_repo_summary_without_clone_attestation_is_not_supported(tmp_path: Path):
    run_dir = tmp_path / "run_partial_repo"
    repo_dir = run_dir / "repos" / "src_repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "repo_brief.md").write_text("# Repository: src_repo\nPython files: 0", encoding="utf-8")
    append_artifact_evidence(
        run_dir,
        source_id="src_repo",
        artifact_path="repos/src_repo/repo_brief.md",
        evidence_type="repo_summary",
        parser_name="repo_summarizer",
        summary="Python files: 0",
    )

    assert load_usable_evidence(run_dir) == []


def test_repo_summarize_requires_successful_clone_attestation(tmp_path: Path):
    run_dir = tmp_path / "run_repo_no_attestation"
    repo_dir = run_dir / "repos" / "src_repo"
    (repo_dir / ".git").mkdir(parents=True)

    ok, outputs = _run_repo_analyze(run_dir, {"source_id": "src_repo"})

    assert ok is False
    assert outputs == []
    assert load_usable_evidence(run_dir) == []
    error_path = run_dir / "sources" / "src_repo" / "repo_summarize_error.json"
    assert error_path.is_file()
    assert "repository acquisition attestation not found" in error_path.read_text(encoding="utf-8")


def test_repo_summarize_job_is_failed_when_git_clone_dependency_failed(tmp_path: Path):
    run_dir = tmp_path / "run_repo_dependency"
    run_dir.mkdir()
    clone_job = append_pipeline_job(run_dir, source_id="src_repo", job_type="git_clone", payload={})
    summarize_job = append_pipeline_job(
        run_dir,
        source_id="src_repo",
        job_type="repo_summarize",
        payload={"depends_on": clone_job["job_id"]},
    )
    fail_pipeline_job(run_dir, clone_job["job_id"], error="network failed")

    processed = _process_pending_jobs(run_dir)
    jobs = load_pipeline_jobs(run_dir)

    assert processed == 1
    assert jobs[1]["job_id"] == summarize_job["job_id"]
    assert jobs[1]["status"] == "failed"
    assert jobs[1]["error"] == f"dependency failed: {clone_job['job_id']}"
