"""Tests for source intake helpers — file upload + source registry."""

import io
from pathlib import Path

import pytest

from autoad_researcher.ui.sources import (
    LEGACY_PARSE_ATTEMPT_ID,
    append_source_ref,
    append_source_parse_attempt,
    find_source_by_stored_path,
    get_allowed_local_source_roots,
    get_source_context,
    load_source_registry,
    register_local_file_source,
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

    def test_upload_name_is_basename_only(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        info = save_uploaded_file(run_dir, _make_upload("../SimpleNet.pdf"))

        assert info["stored_path"].endswith("/SimpleNet.pdf")
        assert ".." not in Path(info["stored_path"]).parts

    def test_returned_source_id_matches_registry(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))
        reg = load_source_registry(run_dir)
        assert reg["sources"][0]["source_id"] == info["source_id"]
        assert info["stored_path"].startswith("sources/" + info["source_id"])
        assert info["stored_path"].endswith("SimpleNet.pdf")


class TestRegisterLocalFileSource:
    def test_registers_server_local_pdf(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(tmp_path))
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        pdf = tmp_path / "2303.15140v2.pdf"
        pdf.write_bytes(b"%PDF fake")

        info = register_local_file_source(run_dir, pdf)

        assert info["kind"] == "paper_pdf"
        assert info["stored_path"].endswith("/2303.15140v2.pdf")
        copied = run_dir / info["stored_path"]
        assert copied.is_file()
        assert copied.read_bytes() == b"%PDF fake"
        reg = load_source_registry(run_dir)
        assert reg["sources"][0]["source_id"] == info["source_id"]
        assert reg["sources"][0]["status"] == "uploaded_not_parsed"

    def test_registers_markdown(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(tmp_path))
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        doc = tmp_path / "notes.md"
        doc.write_text("# Notes", encoding="utf-8")

        info = register_local_file_source(run_dir, doc)

        assert info["kind"] == "markdown"

    def test_registers_text(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(tmp_path))
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        doc = tmp_path / "notes.txt"
        doc.write_text("notes", encoding="utf-8")

        info = register_local_file_source(run_dir, str(doc))

        assert info["kind"] == "text"

    def test_rejects_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(tmp_path))
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        with pytest.raises(ValueError, match="不是可注册的资料文件"):
            register_local_file_source(run_dir, tmp_path / "missing.pdf")

    def test_rejects_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(tmp_path))
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        src = tmp_path / "paper.pdf"
        src.mkdir()

        with pytest.raises(ValueError, match="不是可注册的资料文件"):
            register_local_file_source(run_dir, src)

    def test_rejects_unsupported_file_type(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(tmp_path))
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        src = tmp_path / "archive.zip"
        src.write_bytes(b"zip")

        with pytest.raises(ValueError, match="仅支持"):
            register_local_file_source(run_dir, src)

    def test_default_allowed_root_includes_ai4s(self, monkeypatch):
        monkeypatch.delenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", raising=False)
        roots = get_allowed_local_source_roots()
        assert Path("/root/autodl-tmp/AI4S").resolve() in roots

    def test_rejects_allowlist_outside_path(self, tmp_path, monkeypatch):
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        run_dir = tmp_path / "run_test"
        allowed.mkdir()
        outside.mkdir()
        run_dir.mkdir()
        pdf = outside / "paper.pdf"
        pdf.write_bytes(b"%PDF outside")
        monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(allowed))

        with pytest.raises(ValueError, match="不在允许的资料目录内"):
            register_local_file_source(run_dir, pdf)

    def test_rejects_symlink_escape(self, tmp_path, monkeypatch):
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        run_dir = tmp_path / "run_test"
        allowed.mkdir()
        outside.mkdir()
        run_dir.mkdir()
        target = outside / "paper.pdf"
        target.write_bytes(b"%PDF outside")
        link = allowed / "linked.pdf"
        link.symlink_to(target)
        monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(allowed))

        with pytest.raises(ValueError, match="不在允许的资料目录内"):
            register_local_file_source(run_dir, link)

    def test_env_allowed_roots_are_colon_separated(self, tmp_path, monkeypatch):
        first = tmp_path / "first"
        second = tmp_path / "second"
        run_dir = tmp_path / "run_test"
        first.mkdir()
        second.mkdir()
        run_dir.mkdir()
        pdf = second / "paper.pdf"
        pdf.write_bytes(b"%PDF second")
        monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", f"{first}:{second}")

        roots = get_allowed_local_source_roots()
        assert first.resolve() in roots
        assert second.resolve() in roots
        info = register_local_file_source(run_dir, pdf)
        assert info["kind"] == "paper_pdf"


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

    def test_new_source_ref_has_v04_fields(self, tmp_path):
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
        source = reg["sources"][0]
        assert source["source_id"] == sid
        assert source["intake_status"] == "ok"
        assert source["intake_error"] is None
        assert source["active_parse_attempt_id"] is None
        assert source["parse_attempts"] == []

    def test_source_kind_accepts_v04_reference_kinds(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        for kind in ("webpage", "user_text", "local_repo"):
            append_source_ref(
                run_dir,
                kind=kind,
                user_label=kind,
                stored_path=None,
                status="user_provided_not_ingested",
            )

        reg = load_source_registry(run_dir)
        assert [source["kind"] for source in reg["sources"]] == ["webpage", "user_text", "local_repo"]
        assert [source["intake_status"] for source in reg["sources"]] == ["pending", "pending", "pending"]

    def test_read_legacy_source_references_gets_virtual_attempt(self, tmp_path):
        run_dir = tmp_path / "run_test"
        source_dir = run_dir / "sources"
        source_dir.mkdir(parents=True)
        (run_dir / "paper" / "parse").mkdir(parents=True)
        (run_dir / "paper" / "parse" / "parse_quality_report.json").write_text("{}", encoding="utf-8")
        registry_path = source_dir / "source_references.json"
        registry_path.write_text(
            """{
  "schema_version": 1,
  "sources": [
    {
      "source_id": "src_legacy",
      "kind": "paper_pdf",
      "user_label": "legacy.pdf",
      "status": "parsed",
      "stored_path": "sources/src_legacy/legacy.pdf",
      "created_at": "2026-07-05T00:00:00+00:00"
    }
  ]
}
""",
            encoding="utf-8",
        )

        reg = load_source_registry(run_dir)
        source = reg["sources"][0]
        assert source["active_parse_attempt_id"] == LEGACY_PARSE_ATTEMPT_ID
        assert source["parse_attempts"] == [
            {
                "parse_attempt_id": LEGACY_PARSE_ATTEMPT_ID,
                "parser": "unknown_legacy",
                "status": "ok",
                "output_dir": "paper/parse/",
                "quality_report": "paper/parse/parse_quality_report.json",
            }
        ]

        after_read = registry_path.read_text(encoding="utf-8")
        assert "legacy_active" not in after_read

    def test_legacy_virtual_attempt_is_not_written_on_status_update(self, tmp_path):
        run_dir = tmp_path / "run_test"
        source_dir = run_dir / "sources"
        source_dir.mkdir(parents=True)
        registry_path = source_dir / "source_references.json"
        registry_path.write_text(
            """{
  "schema_version": 1,
  "sources": [
    {
      "source_id": "src_legacy",
      "kind": "paper_pdf",
      "user_label": "legacy.pdf",
      "status": "parsed",
      "stored_path": "sources/src_legacy/legacy.pdf",
      "created_at": "2026-07-05T00:00:00+00:00"
    }
  ]
}
""",
            encoding="utf-8",
        )

        update_source_status(run_dir, "src_legacy", "failed", error_message="parse failed")

        on_disk = registry_path.read_text(encoding="utf-8")
        assert "legacy_active" not in on_disk
        reg = load_source_registry(run_dir)
        source = reg["sources"][0]
        assert source["status"] == "failed"
        assert source["parse_attempts"] == []

    def test_append_source_parse_attempt_does_not_overwrite(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        sid = append_source_ref(
            run_dir,
            kind="paper_pdf",
            user_label="SimpleNet.pdf",
            stored_path="sources/src_001/SimpleNet.pdf",
            status="uploaded_not_parsed",
        )

        append_source_parse_attempt(
            run_dir,
            sid,
            {"parse_attempt_id": "pa_000001", "status": "failed"},
            make_active=False,
        )
        append_source_parse_attempt(
            run_dir,
            sid,
            {"parse_attempt_id": "pa_000002", "status": "ok"},
            make_active=True,
        )

        reg = load_source_registry(run_dir)
        source = reg["sources"][0]
        assert [attempt["parse_attempt_id"] for attempt in source["parse_attempts"]] == ["pa_000001", "pa_000002"]
        assert source["active_parse_attempt_id"] == "pa_000002"

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

    def test_update_to_parsing_status(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        sid = append_source_ref(
            run_dir,
            kind="paper_pdf",
            user_label="SimpleNet.pdf",
            stored_path="sources/src_001/SimpleNet.pdf",
            status="uploaded_not_parsed",
        )
        update_source_status(run_dir, sid, "parsing")
        reg = load_source_registry(run_dir)
        assert reg["sources"][0]["status"] == "parsing"

    def test_find_source_by_stored_path(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))
        found = find_source_by_stored_path(run_dir, info["stored_path"])
        assert found == info["source_id"]

    def test_find_source_nonexistent(self, tmp_path):
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        assert find_source_by_stored_path(run_dir, "sources/nonexistent.pdf") is None


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
