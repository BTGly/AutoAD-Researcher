"""End-to-end tests for Research Assistant v0.4.

Covers the full flow: source registration → parse attempt → context draft
→ freeze → tool guard → legacy compatibility.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoad_researcher.research_context.freeze import freeze_context
from autoad_researcher.ui.sources import (
    _detect_source_kind_from_url,
    append_source_parse_attempt,
    append_source_ref,
    load_source_registry,
    register_url_source,
    register_user_text_source,
)


# ── helpers ──

def _make_run_dir(tmp_path: Path, run_id: str = "run_test_e2e") -> Path:
    d = tmp_path / run_id
    d.mkdir(parents=True)
    (d / "sources").mkdir()
    (d / "paper" / "parse").mkdir(parents=True)
    (d / "context").mkdir()
    return d


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── tests ──


class TestE2EFullFlow:
    """Upload → parse → context → freeze → handoff."""

    def test_e2e_pdf_upload_parse_context_freeze(self, tmp_path: Path):
        """Full flow from source registration through freeze."""
        run_dir = _make_run_dir(tmp_path)

        # 1. register PDF source
        source_id = append_source_ref(
            run_dir, kind="paper_pdf", user_label="paper.pdf",
            stored_path="sources/src_test_paper/paper.pdf",
            status="uploaded_not_parsed",
        )
        assert source_id.startswith("src_")

        # verify source is registered
        reg = load_source_registry(run_dir)
        assert any(s["source_id"] == source_id for s in reg["sources"])

        # 2. simulate parse attempt
        append_source_parse_attempt(
            run_dir, source_id,
            {
                "parse_attempt_id": "pa_000001",
                "source_id": source_id,
                "parser": "mineru",
                "status": "running",
                "output_dir": "paper/parse/attempts/pa_000001/",
                "quality_report": "paper/parse/attempts/pa_000001/parse_quality_report.json",
                "created_at": "2026-07-06T00:00:00Z",
            },
            make_active=False,
        )

        # 3. finalize parse attempt as ok
        from autoad_researcher.ui.sources import update_source_parse_attempt
        update_source_parse_attempt(
            run_dir, source_id, "pa_000001",
            {"status": "ok", "finished_at": "2026-07-06T00:01:00Z", "warnings": []},
            make_active=True,
        )

        # 4. write draft with evidence ref
        draft = _make_draft(run_dir, source_id, "pa_000001")
        (run_dir / "context" / "research_context_draft.json").write_text(
            json.dumps(draft, ensure_ascii=False)
        )

        # 5. freeze
        result = freeze_context(run_dir)
        assert "freeze_version" in result
        assert result["freeze_version"] == "fv_001"

        # 6. verify freeze files exist
        freeze_dir = run_dir / "context" / "freezes" / "fv_001"
        assert freeze_dir.is_dir()
        assert (freeze_dir / "manifest.json").is_file()
        assert (freeze_dir / "source_snapshot.json").is_file()
        assert (freeze_dir / "evidence_boundary.json").is_file()
        assert (freeze_dir / "research_brief.md").is_file()

        # 7. handoff — experiment agents read freeze
        from autoad_researcher.research_context.freeze import active_freeze_context_path
        ctx_path = active_freeze_context_path(run_dir)
        assert ctx_path is not None
        assert str(ctx_path).endswith("research_context_draft.json")

    def test_e2e_parse_failed_recovery_message(self, tmp_path: Path):
        """Failed parse must record source_id + parse_attempt_id + reason."""
        run_dir = _make_run_dir(tmp_path)

        source_id = append_source_ref(
            run_dir, kind="paper_pdf", user_label="bad.pdf",
            stored_path="sources/src_bad/bad.pdf",
            status="uploaded_not_parsed",
        )

        append_source_parse_attempt(
            run_dir, source_id,
            {
                "parse_attempt_id": "pa_000001",
                "source_id": source_id,
                "parser": "mineru",
                "status": "running",
                "output_dir": "paper/parse/attempts/pa_000001/",
                "quality_report": "paper/parse/attempts/pa_000001/parse_quality_report.json",
                "created_at": "2026-07-06T00:00:00Z",
            },
            make_active=False,
        )

        from autoad_researcher.ui.sources import update_source_parse_attempt
        update_source_parse_attempt(
            run_dir, source_id, "pa_000001",
            {"status": "failed", "finished_at": "2026-07-06T00:01:00Z", "warnings": ["pdf corrupted"]},
            make_active=False,
        )

        reg = load_source_registry(run_dir)
        spans = [s for s in reg["sources"] if s["source_id"] == source_id]
        assert len(spans) == 1
        attempts = spans[0].get("parse_attempts", [])
        assert len(attempts) == 1
        assert attempts[0]["parse_attempt_id"] == "pa_000001"
        assert attempts[0]["status"] == "failed"
        # source-level status aggregates: failed parse attempt with make_active=False
        # leaves source status unchanged (no active attempt to represent)
        assert spans[0]["active_parse_attempt_id"] is None

    def test_e2e_multi_parse_attempt_does_not_overwrite(self, tmp_path: Path):
        """3 parse attempts: pa_001 ok, pa_002 failed, pa_003 partial — all retained."""
        run_dir = _make_run_dir(tmp_path)

        source_id = append_source_ref(
            run_dir, kind="paper_pdf", user_label="paper.pdf",
            stored_path="sources/src_multi/paper.pdf",
            status="uploaded_not_parsed",
        )

        from autoad_researcher.ui.sources import update_source_parse_attempt

        for pa_id, status, active in [
            ("pa_000001", "ok", True),
            ("pa_000002", "failed", False),
            ("pa_000003", "partial", False),
        ]:
            append_source_parse_attempt(
                run_dir, source_id,
                {
                    "parse_attempt_id": pa_id,
                    "source_id": source_id,
                    "parser": "mineru",
                    "status": "running",
                    "output_dir": f"paper/parse/attempts/{pa_id}/",
                    "quality_report": f"paper/parse/attempts/{pa_id}/parse_quality_report.json",
                    "created_at": "2026-07-06T00:00:00Z",
                },
                make_active=False,
            )
            update_source_parse_attempt(
                run_dir, source_id, pa_id,
                {"status": status, "finished_at": "2026-07-06T00:01:00Z", "warnings": []},
                make_active=active,
            )

        reg = load_source_registry(run_dir)
        spans = [s for s in reg["sources"] if s["source_id"] == source_id]
        attempts = spans[0].get("parse_attempts", [])
        assert len(attempts) == 3
        assert attempts[0]["parse_attempt_id"] == "pa_000001"
        assert attempts[1]["parse_attempt_id"] == "pa_000002"
        assert attempts[2]["parse_attempt_id"] == "pa_000003"
        # active should still be pa_000001 (failed/partial don't replace ok)
        assert spans[0].get("active_parse_attempt_id") == "pa_000001"


class TestE2ESourceIntake:
    """Source registration for GitHub, webpage, and user text."""

    def test_e2e_github_source_registered_intake_pending(self, tmp_path: Path):
        run_dir = _make_run_dir(tmp_path)
        result = register_url_source(run_dir, "https://github.com/amazon-science/patchcore-inspection")
        assert result["kind"] == "github_repo"
        assert result["intake_status"] == "pending"
        assert result["status"] == "user_provided_not_ingested"

        reg = load_source_registry(run_dir)
        matches = [s for s in reg["sources"] if s["kind"] == "github_repo"]
        assert len(matches) == 1
        assert matches[0]["intake_status"] == "pending"

    def test_e2e_webpage_source_registered_intake_pending(self, tmp_path: Path):
        run_dir = _make_run_dir(tmp_path)
        result = register_url_source(run_dir, "https://arxiv.org/abs/2106.08265")
        assert result["kind"] == "webpage"
        assert result["intake_status"] == "pending"

    def test_e2e_user_text_source_becomes_evidence(self, tmp_path: Path):
        run_dir = _make_run_dir(tmp_path)
        text = "目标数据集为 MVTec AD，关注纹理类异常检测，baseline 使用 PatchCore。"
        result = register_user_text_source(run_dir, text)
        assert result["source_id"].startswith("src_")
        assert "sha256" in result

        reg = load_source_registry(run_dir)
        matches = [s for s in reg["sources"] if s["kind"] == "user_text"]
        assert len(matches) == 1
        assert matches[0]["status"] == "parsed"
        assert matches[0]["intake_status"] == "ok"
        # file should exist
        stored = matches[0]["stored_path"]
        assert stored is not None
        assert (run_dir / stored).read_text(encoding="utf-8") == text

    def test_e2e_url_kind_detection(self):
        """URL kind detection: github → github_repo, else → webpage."""
        assert _detect_source_kind_from_url("https://github.com/user/repo") == "github_repo"
        assert _detect_source_kind_from_url("https://GITHUB.COM/user/repo/issues/1") == "github_repo"
        assert _detect_source_kind_from_url("https://arxiv.org/abs/2106.08265") == "webpage"
        assert _detect_source_kind_from_url("https://example.com/paper.html") == "webpage"


class TestE2EToolGuardAndLegacy:
    """Tool guard rejection and legacy run compatibility."""

    def test_e2e_legacy_run_reads_without_parse_attempts(self, tmp_path: Path):
        """Old source_references.json without parse_attempts must not crash."""
        run_dir = _make_run_dir(tmp_path)
        reg = {"schema_version": 1, "sources": [
            {"source_id": "src_old", "kind": "paper_pdf", "user_label": "old.pdf",
             "status": "parsed", "stored_path": "sources/src_old/old.pdf", "created_at": "2026-01-01T00:00:00Z"}
        ]}
        (run_dir / "sources" / "source_references.json").write_text(json.dumps(reg))

        # reading should auto-create legacy_active virtual attempt
        loaded = load_source_registry(run_dir)
        assert len(loaded["sources"]) == 1
        source = loaded["sources"][0]
        attempts = source.get("parse_attempts", [])
        assert len(attempts) == 1
        assert attempts[0]["parse_attempt_id"] == "legacy_active"
        assert attempts[0]["status"] == "ok"

    def test_e2e_freeze_blocks_runner_execute_tool_guard(self, tmp_path: Path):
        """ToolGuard must reject runner_execute. Simulated via response guard pattern."""
        run_dir = _make_run_dir(tmp_path)

        from autoad_researcher.assistant.response_guard import guard_research_chat_reply
        from autoad_researcher.assistant.research_context_builder import ResearchChatEvidenceContext

        evidence = ResearchChatEvidenceContext(
            has_parsed_paper_evidence=True,
            has_repo_evidence=False,
        )

        # reply promising execution without approval → must be flagged
        guarded = guard_research_chat_reply(
            reply="我现在就运行 benchmark 测试。",
            user_input="跑一次 benchmark",
            evidence_context=evidence,
            execution_approved=False,
        )
        assert "execution_promise_without_approval" in guarded.violations

        # reply mentioning paper content with evidence → ok
        guarded2 = guard_research_chat_reply(
            reply="论文提出了一种基于特征记忆库的方法。",
            user_input="论文说了什么方法",
            evidence_context=evidence,
            execution_approved=False,
        )
        assert "paper_content_without_parsed_artifact" not in guarded2.violations

    def test_e2e_response_guard_blocks_unparsed_content_claim(self, tmp_path: Path):
        run_dir = _make_run_dir(tmp_path)

        from autoad_researcher.assistant.response_guard import guard_research_chat_reply
        from autoad_researcher.assistant.research_context_builder import ResearchChatEvidenceContext

        evidence = ResearchChatEvidenceContext(
            has_parsed_paper_evidence=False,
            has_repo_evidence=False,
        )

        guarded = guard_research_chat_reply(
            reply="这篇论文提出了一种新颖的异常检测方法，使用 coreset 采样。",
            user_input="介绍一下论文方法",
            evidence_context=evidence,
            execution_approved=False,
        )
        assert "paper_content_without_parsed_artifact" in guarded.violations


# ── draft helper ──

def _make_draft(run_dir: Path, source_id: str, pa_id: str) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_dir.name,
        "context_id": f"ctx_{run_dir.name}_0",
        "context_version": 0,
        "task": {"task_id": f"task_{run_dir.name}", "goal": "e2e test"},
        "sources": {"paper_source_id": source_id},
        "facts": [
            {
                "fact_id": "fact_001",
                "fact_type": "paper_fact",
                "subject": "method",
                "predicate": "mentions",
                "value": "coreset",
                "status": "confirmed",
                "evidence_ids": ["ev_001"],
                "evidence_refs": [
                    {
                        "source_id": source_id,
                        "parse_attempt_id": pa_id,
                        "artifact": f"paper/parse/attempts/{pa_id}",
                        "evidence_type": "parsed_full_text",
                    }
                ],
                "producer_stage": "3.2_paper_intelligence",
            }
        ],
        "gaps": [],
        "conflicts": [],
        "readiness": {
            "status": "ready_for_idea_transfer_design",
            "next_stage": "3.4_idea_transfer_design",
        },
        "source_evidence": [
            {
                "source_id": source_id,
                "parse_attempt_id": pa_id,
                "artifact": f"paper/parse/attempts/{pa_id}",
                "evidence_type": "parsed_full_text",
            }
        ],
        "evidence_boundary": {
            "unparsed_sources": [],
            "partial_parse_attempts": [],
            "failed_parse_attempts": [],
            "claims_not_supported": [],
        },
    }
