"""Tests for legacy/error-path hardening: sanitizer, source_id, dedup."""
import json
import re
from pathlib import Path

import pytest

from autoad_researcher.paper_intelligence.ids import (
    IdentifierPattern,
    LegacyPaperSourceIdPattern,
)
from autoad_researcher.ui.research_chat import (
    _sanitize_response_context_for_llm,
)
from autoad_researcher.ui.sources import _generate_source_id


# ── Bug 1: sanitizer ──

def test_llm_context_does_not_include_unknown_legacy():
    ctx = {
        "facts": {
            "parse_attempts": [
                {"parse_attempt_id": "pa_001", "parser": "unknown_legacy", "status": "ok"},
                {"parse_attempt_id": "pa_002", "parser": "mineru", "status": "failed"},
            ]
        }
    }
    clean = _sanitize_response_context_for_llm(ctx)
    attempts = clean["facts"]["parse_attempts"]
    assert "parser" not in attempts[0]
    assert attempts[1]["parser"] == "mineru"


def test_llm_context_does_not_include_legacy_parse_attempt_flag():
    ctx = {
        "facts": {
            "parse_attempts": [
                {"parse_attempt_id": "pa_001", "parser": "unknown_legacy", "status": "ok"},
            ]
        }
    }
    clean = _sanitize_response_context_for_llm(ctx)
    attempt = clean["facts"]["parse_attempts"][0]
    assert "legacy_parse_attempt" not in attempt
    assert "parser" not in attempt


# ── Bug 2: source_id ──

def test_new_source_id_is_identifier_safe():
    sid = _generate_source_id()
    assert re.match(IdentifierPattern, sid), f"new id {sid} does not match IdentifierPattern"
    assert "+" not in sid, f"new id {sid} contains '+'"
    assert sid.startswith("src_"), f"new id {sid} must start with src_"


def test_paper_source_accepts_legacy_source_id_with_colon_and_plus():
    from autoad_researcher.paper_intelligence.models import PaperSource
    from datetime import datetime, timezone

    legacy_id = "src_2026-07-05T13-54-04-413814+00-00"
    assert re.match(LegacyPaperSourceIdPattern, legacy_id), \
        f"legacy id {legacy_id} should match LegacyPaperSourceIdPattern"
    assert not re.match(IdentifierPattern, legacy_id), \
        f"legacy id {legacy_id} should NOT match strict IdentifierPattern"

    source = PaperSource(
        schema_version=1,
        source_id=legacy_id,
        source_kind="user_pdf",
        original_filename_label="test.pdf",
        storage_path_label="sources/test/test.pdf",
        source_pdf_sha256="a" * 64,
        size_bytes=1024,
        mime_type="application/pdf",
        created_at=datetime.now(timezone.utc),
    )
    assert source.source_id == legacy_id


def test_paper_source_identifier_pattern_is_not_globally_relaxed():
    from autoad_researcher.paper_intelligence.parser_models import (
        DocumentParseRequest,
        ParserManifest,
    )
    from datetime import datetime, timezone

    safe_id = "src_test_001"
    unsafe_id = "src:bad+id"

    # DocumentParseRequest.parser_profile_id still uses IdentifierPattern
    with pytest.raises(Exception):
        DocumentParseRequest(
            schema_version=1,
            source_id=safe_id,
            source_pdf_path="test.pdf",
            parser_profile_id=unsafe_id,  # should fail
            ocr_policy="auto",
            max_pages=100,
            max_runtime_seconds=300,
        )

    # ParserManifest.parser_profile_id still uses IdentifierPattern
    with pytest.raises(Exception):
        ParserManifest(
            schema_version=1,
            parser_name="MinerU",
            parser_version="1.0",
            parser_backend="pipeline",
            parser_profile_id=unsafe_id,  # should fail
            parser_profile_sha256="a" * 64,
            runtime_python_version="3.12",
            runtime_platform="linux",
            device_profile="cpu",
            source_pdf_sha256="a" * 64,
            canonical_output_sha256="b" * 64,
        )


# ── Bug 3: dedup ──

def test_repeated_parse_failure_is_not_retriggered_without_force(tmp_path):
    """A failed source without force_reparse should skip CLI."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "sources").mkdir()

    sid = _generate_source_id()
    registry = {
        "schema_version": 1,
        "sources": [
            {
                "source_id": sid,
                "kind": "paper_pdf",
                "status": "failed",
                "error_message": "status=blocked; stage=preflight",
                "user_label": "paper.pdf",
                "stored_path": "sources/test/paper.pdf",
                "created_at": "2026-01-01T00:00:00Z",
                "parse_attempts": [],
            }
        ],
    }
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps(registry)
    )

    from autoad_researcher.ui.research_chat import _execute_or_report_pdf_parse_action

    reply = _execute_or_report_pdf_parse_action(
        run_dir,
        {"action": "parse", "pdf_path": str(run_dir / "sources" / "test" / "paper.pdf"), "source_id": sid},
    )
    assert "没有重复触发解析" in reply


def test_force_reparse_allows_retry_after_failed_attempt(tmp_path, monkeypatch):
    """force_reparse should bypass dedup and call CLI."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "sources").mkdir()

    sid = _generate_source_id()
    pdf_dir = run_dir / "sources" / sid
    pdf_dir.mkdir(parents=True)
    pdf_path = pdf_dir / "paper.pdf"
    pdf_path.write_text("fake pdf")

    registry = {
        "schema_version": 1,
        "sources": [
            {
                "source_id": sid,
                "kind": "paper_pdf",
                "status": "failed",
                "error_message": "test failure",
                "user_label": "paper.pdf",
                "stored_path": str(pdf_path.relative_to(run_dir)),
                "created_at": "2026-01-01T00:00:00Z",
                "parse_attempts": [],
            }
        ],
    }
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps(registry)
    )

    # Mock _run_paper_intelligence to avoid real CLI
    was_called = False

    def fake_run(run_id, pdf_p):
        nonlocal was_called
        was_called = True
        return {"status": "parsed"}

    import autoad_researcher.ui.research_chat as mod
    orig = getattr(mod, "_run_paper_intelligence", None)
    mod._run_paper_intelligence = fake_run
    try:
        from autoad_researcher.ui.research_chat import _execute_or_report_pdf_parse_action
        _execute_or_report_pdf_parse_action(
            run_dir,
            {"action": "parse", "pdf_path": str(pdf_path), "source_id": sid},
            user_input="强制重新解析",
        )
    finally:
        if orig:
            mod._run_paper_intelligence = orig

    assert was_called, "force reparse should call _run_paper_intelligence"


def test_repeated_parse_error_message_is_not_duplicated(tmp_path):
    """Dedup should return a short message, not repeat the full error."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "sources").mkdir()

    sid = _generate_source_id()
    registry = {
        "schema_version": 1,
        "sources": [
            {
                "source_id": sid,
                "kind": "paper_pdf",
                "status": "failed",
                "error_message": "returncode=1",
                "user_label": "paper.pdf",
                "stored_path": "sources/test/paper.pdf",
                "created_at": "2026-01-01T00:00:00Z",
                "parse_attempts": [],
            }
        ],
    }
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps(registry)
    )

    from autoad_researcher.ui.research_chat import _execute_or_report_pdf_parse_action

    reply = _execute_or_report_pdf_parse_action(
        run_dir,
        {"action": "parse", "pdf_path": str(run_dir / "sources" / "test" / "paper.pdf"), "source_id": sid},
    )
    assert len(reply) < 200
    assert "returncode=1" not in reply
