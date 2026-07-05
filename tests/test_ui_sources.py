"""Tests for source intake helpers — file upload + source registry."""

import io
from pathlib import Path

import pytest

from autoad_researcher.ui.sources import (
    append_source_ref,
    get_source_context,
    load_source_registry,
    resolve_source_pdf_path_safely,
    save_uploaded_file,
    update_source_status,
)


def _make_upload(name: str, content: bytes = b"fake pdf content"):
    """Return an object matching st.file_uploader's UploadedFile duck type."""
    uploaded = io.BytesIO(content)
    uploaded.name = name
    uploaded.getvalue = lambda: content
    return uploaded


class TestSaveUploadedFile:
    def test_saves_pdf_and_writes_registry(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        upload = _make_upload("SimpleNet.pdf")

        info = save_uploaded_file(run_dir, upload)
        assert info["source_id"].startswith("src_")
        assert info["kind"] == "paper_pdf"

        # File exists
        full = run_dir / info["stored_path"]
        assert full.is_file()
        assert full.read_bytes() == b"fake pdf content"

        # Registry
        reg = load_source_registry(run_dir)
        assert len(reg["sources"]) == 1
        assert reg["sources"][0]["status"] == "uploaded_not_parsed"
        assert reg["sources"][0]["user_label"] == "SimpleNet.pdf"

    def test_saves_markdown_and_writes_registry(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        upload = _make_upload("notes.md", b"# Notes")

        info = save_uploaded_file(run_dir, upload)
        assert info["kind"] == "markdown"


class TestSourceRegistry:
    def test_load_empty_registry(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        reg = load_source_registry(run_dir)
        assert reg["sources"] == []

    def test_append_and_update(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        sid = append_source_ref(
            run_dir,
            kind="paper_pdf",
            user_label="SimpleNet.pdf",
            stored_path="sources/src_001/SimpleNet.pdf",
            status="uploaded_not_parsed",
        )

        reg = load_source_registry(run_dir)
        assert len(reg["sources"]) == 1

        update_source_status(run_dir, sid, "parsed")
        reg = load_source_registry(run_dir)
        assert reg["sources"][0]["status"] == "parsed"

    def test_update_with_error_message(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        sid = append_source_ref(
            run_dir,
            kind="paper_pdf",
            user_label="bad.pdf",
            stored_path="sources/bad.pdf",
            status="uploaded_not_parsed",
        )
        update_source_status(run_dir, sid, "failed", error_message="MinerU timeout")
        reg = load_source_registry(run_dir)
        assert reg["sources"][0]["status"] == "failed"
        assert reg["sources"][0]["error_message"] == "MinerU timeout"


class TestSourceContext:
    def test_empty_context(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        assert get_source_context(run_dir) == ""

    def test_context_contains_status(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))
        ctx = get_source_context(run_dir)
        assert "uploaded_not_parsed" in ctx
        assert "SimpleNet.pdf" in ctx
        assert "SourceReferences" in ctx


class TestResolvePath:
    def test_valid_path_resolves(self, tmp_path):
        run_dir = tmp_path / "run_test"
        (run_dir / "sources").mkdir(parents=True)
        (run_dir / "sources" / "test.pdf").write_bytes(b"x")
        result = resolve_source_pdf_path_safely(run_dir, "读一下 sources/test.pdf")
        assert result is not None
        assert result.name == "test.pdf"

    def test_parent_traversal_rejected(self, tmp_path):
        run_dir = tmp_path / "run_test"
        (run_dir / "sources").mkdir(parents=True)
        result = resolve_source_pdf_path_safely(run_dir, "读一下 sources/../../../etc/passwd.pdf")
        assert result is None

    def test_absolute_path_rejected(self, tmp_path):
        run_dir = tmp_path / "run_test"
        (run_dir / "sources").mkdir(parents=True)
        result = resolve_source_pdf_path_safely(run_dir, "读一下 /etc/passwd.pdf")
        assert result is None

    def test_nonexistent_pdf(self, tmp_path):
        run_dir = tmp_path / "run_test"
        (run_dir / "sources").mkdir(parents=True)
        result = resolve_source_pdf_path_safely(run_dir, "读一下 sources/missing.pdf")
        assert result is None

    def test_no_pdf_in_text(self, tmp_path):
        run_dir = tmp_path / "run_test"
        (run_dir / "sources").mkdir(parents=True)
        result = resolve_source_pdf_path_safely(run_dir, "我想复现论文")
        assert result is None
