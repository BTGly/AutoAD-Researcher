"""Paper intelligence and unified research context behavior tests.

Tests the deterministic contract layer without real LLM calls.
All tests that interact with workspace-relative paths use chdir into
the tmp_path fixture so that workspace-relative paths resolve correctly.
"""

import json
import os
from pathlib import Path

import pytest

from autoad_researcher.paper_intelligence.attestation import (
    attest_paper_source,
    check_pdf_magic,
    SOURCE_FAILURE_CODES,
)
from autoad_researcher.paper_intelligence.errors import PaperSourceError
from autoad_researcher.paper_intelligence.mineru_provider import (
    FixtureMinerUProvider,
    MINERU_PIPELINE_V1_PROFILE,
    _deterministic_attempt_id,
)
from autoad_researcher.paper_intelligence.parser_models import (
    DocumentParseRequest,
)
from autoad_researcher.paper_intelligence.models import (
    PaperClaim,
    PaperMentionedCandidate,
)
from autoad_researcher.paper_intelligence.validator import (
    validate_claim,
    validate_candidate,
    validate_candidate_not_selected,
    validate_page_index,
    PaperValidationReport,
)
from autoad_researcher.paper_intelligence.repair import (
    repair_claim,
    repair_candidate,
    run_paper_repair,
)
from autoad_researcher.research_context.assembly import (
    assemble_fact_ledger,
    classify_gaps,
    compute_readiness,
    detect_conflicts,
    finalize_research_context,
)
from autoad_researcher.research_context.models import (
    TaskContext,
    ContextReadiness,
    ResearchContext,
    SourceContext,
)
from autoad_researcher.paper_intelligence.agent import budget_for_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_pdf(path: Path):
    path.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Page>>endobj\n%%EOF")


def _chdir_tmp(tmp_path, work):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return work()
    finally:
        os.chdir(cwd)


# ---------------------------------------------------------------------------
# P0-2: Deterministic MinerU Provider
# ---------------------------------------------------------------------------


class TestDeterministicMinerU:
    def test_deterministic_attempt_id(self):
        id1 = _deterministic_attempt_id("src_a", "aaaa" * 16, "bbbb" * 16)
        id2 = _deterministic_attempt_id("src_a", "aaaa" * 16, "bbbb" * 16)
        assert id1 == id2, "same inputs must produce same attempt id"

    def test_different_source_yields_different_id(self):
        id1 = _deterministic_attempt_id("src_a", "aaaa" * 16, "bbbb" * 16)
        id2 = _deterministic_attempt_id("src_b", "aaaa" * 16, "bbbb" * 16)
        assert id1 != id2

    def test_parse_writes_canonical_artifacts(self, tmp_path):
        provider = FixtureMinerUProvider(
            profile=MINERU_PIPELINE_V1_PROFILE,
            runtime_python_version="3.10",
            runtime_platform="linux",
            device_profile="cpu",
        )

        def work():
            pdf = Path("test.pdf")
            _make_valid_pdf(pdf)
            output_dir = Path("parse_output")
            req = DocumentParseRequest(
                schema_version=1, source_id="src_test",
                source_pdf_path="test.pdf",
                parser_profile_id="mineru_pipeline_v1",
                ocr_policy="auto", max_pages=40, max_runtime_seconds=600,
            )
            result = provider.parse(req, output_dir)
            assert result.status == "success"
            assert result.parse_attempt_id.startswith("pa_")
            assert (output_dir / "pages.jsonl").exists()
            assert (output_dir / "sections.json").exists()
            assert (output_dir / "parser_manifest.json").exists()
            assert (output_dir / "parse_quality_report.json").exists()
            assert (output_dir / "canonical_output.sha256").exists()

        _chdir_tmp(tmp_path, work)

    def test_parse_failure_on_missing_pdf(self, tmp_path):
        provider = FixtureMinerUProvider(
            profile=MINERU_PIPELINE_V1_PROFILE,
            runtime_python_version="3.10",
            runtime_platform="linux",
            device_profile="cpu",
        )

        def work():
            output_dir = Path("parse_output")
            req = DocumentParseRequest(
                schema_version=1, source_id="src_test",
                source_pdf_path="missing.pdf",
                parser_profile_id="mineru_pipeline_v1",
                ocr_policy="auto", max_pages=40, max_runtime_seconds=600,
            )
            result = provider.parse(req, output_dir)
            assert result.status == "failed"
            assert "source PDF not found" in result.warnings[0]

        _chdir_tmp(tmp_path, work)

    def test_manifest_uses_real_hashes(self, tmp_path):
        provider = FixtureMinerUProvider(
            profile=MINERU_PIPELINE_V1_PROFILE,
            runtime_python_version="3.10",
            runtime_platform="linux",
            device_profile="cpu",
        )

        def work():
            pdf = Path("test.pdf")
            _make_valid_pdf(pdf)
            output_dir = Path("parse_output")
            req = DocumentParseRequest(
                schema_version=1, source_id="src_test",
                source_pdf_path="test.pdf",
                parser_profile_id="mineru_pipeline_v1",
                ocr_policy="auto", max_pages=40, max_runtime_seconds=600,
            )
            result = provider.parse(req, output_dir)
            manifest = provider.get_manifest(result)
            assert len(manifest.source_pdf_sha256) == 64
            assert manifest.source_pdf_sha256 != "fixture"
            assert manifest.canonical_output_sha256 != "fixture"

        _chdir_tmp(tmp_path, work)


# ---------------------------------------------------------------------------
# P0-2: Source Attestation
# ---------------------------------------------------------------------------


class TestSourceAttestation:
    def test_accepts_valid_pdf_magic(self, tmp_path):
        def work():
            pdf = Path("test.pdf")
            _make_valid_pdf(pdf)
            result = attest_paper_source("test.pdf", "test.pdf")
            assert result["size_bytes"] > 0
            assert len(result["source_pdf_sha256"]) == 64

        _chdir_tmp(tmp_path, work)

    def test_rejects_non_pdf_magic(self, tmp_path):
        def work():
            txt = Path("fake.pdf")
            txt.write_text("not a real PDF")
            with pytest.raises(PaperSourceError, match="PAPER_SOURCE_NOT_PDF"):
                attest_paper_source("fake.pdf", "fake.pdf")

        _chdir_tmp(tmp_path, work)

    def test_rejects_outside_workspace(self, tmp_path):
        def work():
            with pytest.raises(PaperSourceError, match="PAPER_SOURCE_OUTSIDE_WORKSPACE"):
                attest_paper_source("/etc/passwd.pdf", "hack.pdf")

        _chdir_tmp(tmp_path, work)

    def test_magic_check_function(self, tmp_path):
        def work():
            pdf = Path("test.pdf")
            _make_valid_pdf(pdf)
            assert check_pdf_magic(pdf) is True

        _chdir_tmp(tmp_path, work)

    def test_magic_check_non_pdf(self, tmp_path):
        def work():
            txt = Path("text.pdf")
            txt.write_text("Hello world")
            assert check_pdf_magic(txt) is False

        _chdir_tmp(tmp_path, work)


# ---------------------------------------------------------------------------
# P0-4: Evidence Validator
# ---------------------------------------------------------------------------


class TestEvidenceValidator:
    def test_confirmed_requires_evidence(self):
        claim = PaperClaim(
            claim_id="c1", subject="method", predicate="uses",
            value="X", status="confirmed", confidence="high",
            evidence_ids=[],
        )
        issues = validate_claim(claim)
        assert len(issues) >= 1
        assert "evidence" in issues[0].issue.lower()

    def test_confirmed_with_evidence_passes(self):
        claim = PaperClaim(
            claim_id="c1", subject="method", predicate="uses",
            value="X", status="confirmed", confidence="high",
            evidence_ids=["ev_001"],
        )
        issues = validate_claim(claim)
        assert len(issues) == 0

    def test_inferred_requires_rationale(self):
        claim = PaperClaim(
            claim_id="c1", subject="method", predicate="uses",
            value="X", status="inferred", confidence="medium",
        )
        issues = validate_claim(claim)
        assert len(issues) >= 1
        assert "rationale" in issues[0].issue.lower()

    def test_conflicting_requires_two_evidence(self):
        claim = PaperClaim(
            claim_id="c1", subject="method", predicate="uses",
            value="X", status="conflicting", confidence="low",
            evidence_ids=["ev_001"],
        )
        issues = validate_claim(claim)
        assert len(issues) >= 1
        assert "two" in issues[0].issue.lower()

    def test_candidate_must_be_paper_mentioned(self):
        cand = PaperMentionedCandidate(
            candidate_id="c1", kind="baseline", name="X",
            mention_role="compared_baseline",
            selection_status="paper_mentioned",
        )
        issues = validate_candidate(cand)
        assert len(issues) == 0

    def test_validate_candidate_not_selected_detects_mutation(self):
        cand = PaperMentionedCandidate(
            candidate_id="c1", kind="baseline", name="X",
            mention_role="compared_baseline",
            selection_status="paper_mentioned",
        )
        assert validate_candidate_not_selected(cand) is True

    def test_page_index_validation(self):
        assert validate_page_index(0, 12) is True
        assert validate_page_index(11, 12) is True
        assert validate_page_index(12, 12) is False
        assert validate_page_index(-1, 12) is False


# ---------------------------------------------------------------------------
# P0-4: Repair
# ---------------------------------------------------------------------------


class TestRepair:
    def test_repair_downgrades_unsupported_claim(self):
        claim = PaperClaim(
            claim_id="c1", subject="method", predicate="uses",
            value="X", status="confirmed", confidence="high",
            evidence_ids=[],
        )
        issue = validate_claim(claim)[0]
        repaired = repair_claim(claim, issue)
        assert repaired is not None
        assert repaired.status == "unknown"

    def test_repair_resets_candidate_selection(self):
        from autoad_researcher.paper_intelligence.validator import CandidateValidationIssue
        cand = PaperMentionedCandidate(
            candidate_id="c1", kind="baseline", name="X",
            mention_role="compared_baseline",
            selection_status="paper_mentioned",
        )
        issue = CandidateValidationIssue(
            candidate_id="c1",
            issue="selection_status must be 'paper_mentioned', got 'selected'",
            severity="error",
        )
        repaired = repair_candidate(cand, issue)
        assert repaired is not None
        assert repaired.selection_status == "paper_mentioned"
        assert "repaired" in repaired.warnings[-1].lower()

    def test_repair_respects_budget(self):
        budget = budget_for_profile("short")
        claims = [
            PaperClaim(claim_id=f"c{i}", subject="x", predicate="y",
                      value="z", status="confirmed", confidence="high",
                      evidence_ids=[])
            for i in range(10)
        ]
        issues = []
        for c in claims:
            issues.extend(validate_claim(c))
        report = PaperValidationReport(valid=False, claim_issues=issues)
        repaired, _, repairs = run_paper_repair(claims, [], report, budget)
        assert repairs <= budget.max_repairs
        assert repairs == budget.max_repairs


# ---------------------------------------------------------------------------
# P0-5: Context Readiness (false-ready prevention)
# ---------------------------------------------------------------------------


class TestContextReadiness:
    def test_no_gaps_returns_ready(self):
        readiness = compute_readiness([], [])
        assert readiness.status == "ready_for_idea_transfer_design"
        assert readiness.next_stage == "3.4_idea_transfer_design"

    def test_paper_evidence_gap_blocks_ready(self):
        from autoad_researcher.research_context.models import InformationGap
        gap = InformationGap(
            gap_id="gap_pe", gap_type="paper_evidence_required",
            category="reader_reanalysis_needed", severity="high",
            question_needed=False, reason="no paper facts",
            downstream_impact="no evidence-backed claims",
            resolution_target="paper_intelligence",
        )
        readiness = compute_readiness([gap], [])
        assert readiness.status == "needs_reader_reanalysis"
        assert readiness.next_stage == "3.2_reanalysis"
        assert "paper_intelligence" in readiness.reanalysis_targets

    def test_user_decision_gap_triggers_3_3(self):
        from autoad_researcher.research_context.models import InformationGap
        gap = InformationGap(
            gap_id="gap_ud", gap_type="user_decision_required",
            category="baseline_selection", severity="blocking",
            question_needed=True, reason="no baseline selected",
            downstream_impact="cannot design experiment",
            resolution_target="3.3_context_repair",
        )
        readiness = compute_readiness([gap], [])
        assert readiness.status == "needs_clarification"
        assert readiness.next_stage == "3.3_context_repair"

    def test_policy_conflict_blocks(self):
        from autoad_researcher.research_context.models import InformationGap
        gap = InformationGap(
            gap_id="gap_pc", gap_type="system_policy_conflict",
            category="policy_conflict", severity="blocking",
            question_needed=False, reason="policy violation",
            downstream_impact="execution forbidden",
            resolution_target="stop",
        )
        readiness = compute_readiness([gap], [])
        assert readiness.status == "blocked_by_policy"
        assert readiness.next_stage == "stop"


# ---------------------------------------------------------------------------
# U2-U3: Fact Ledger and Gaps
# ---------------------------------------------------------------------------


class TestFactLedgerAndGaps:
    def test_assemble_rejects_duplicate_ids(self):
        with pytest.raises(ValueError, match="Duplicate fact_id"):
            assemble_fact_ledger(
                paper_facts=[
                    {"fact_id": "f1", "subject": "x", "predicate": "y",
                     "value": "z", "status": "confirmed"},
                ],
                repository_facts=[
                    {"fact_id": "f1", "subject": "x", "predicate": "y",
                     "value": "z", "status": "confirmed"},
                ],
            )

    def test_paper_facts_without_paper_facts_triggers_gap(self):
        facts = assemble_fact_ledger()
        task = TaskContext(task_id="t1", goal="test")
        gaps = classify_gaps(facts, task)
        paper_gaps = [g for g in gaps if g.gap_type == "paper_evidence_required"]
        assert len(paper_gaps) >= 1
        assert paper_gaps[0].severity == "high"

    def test_conflict_detection_empty(self):
        conflicts = detect_conflicts([])
        assert len(conflicts) == 0

    def test_finalize_makes_copy(self):
        task = TaskContext(task_id="t1", goal="test")
        readiness = ContextReadiness(
            status="ready_for_idea_transfer_design",
            next_stage="3.4_idea_transfer_design",
        )
        ctx = ResearchContext(
            schema_version=1, run_id="r1", context_id="c1",
            context_version=0, task=task, sources=SourceContext(),
            readiness=readiness, context_sha256="a" * 64,
        )
        finalized = finalize_research_context(ctx, readiness)
        assert finalized.context_version == 1
        assert finalized is not ctx
        assert ctx.context_version == 0


# ---------------------------------------------------------------------------
# P0-6: Orchestrator E2E behavior
# ---------------------------------------------------------------------------


class TestOrchestratorBehavior:
    def test_e2e_with_valid_pdf(self, tmp_path):
        from autoad_researcher.paper_intelligence.orchestrator import PaperIntelligenceOrchestrator
        from autoad_researcher.paper_intelligence.models import PaperIntelligenceRequest
        from autoad_researcher.paper_intelligence.agent import budget_for_profile
        import json

        def work():
            pdf = Path("test.pdf")
            _make_valid_pdf(pdf)
            budget = budget_for_profile("standard")
            req = PaperIntelligenceRequest(
                schema_version=1, request_id="req_t", run_id="test_run",
                user_goal="Test paper analysis",
                paper_pdf_path="test.pdf",
                parser_profile_id="mineru_pipeline_v1",
                web_context_allowed=False, alpha_xiv_allowed=False,
                user_confirmation_policy="never",
                budget_profile="standard", budget=budget,
            )
            orch = PaperIntelligenceOrchestrator(Path("runs"))
            result = orch.run(req)
            assert result["status"] in ("success", "partial_success")

            # Hard assertions per review
            assert result["evidence_ref_count"] > 0, "evidence_ref_count must be > 0"
            assert result["unsupported_claim_count"] == 0, "no confirmed claim should lack evidence"
            assert result["claim_count"] > 0
            assert result["candidate_count"] >= 0

            # evidence_index.jsonl must exist and have entries
            ev_path = Path("runs/test_run/paper/evidence_index.jsonl")
            assert ev_path.exists(), "evidence_index.jsonl missing"
            lines = [l for l in ev_path.read_text().split("\n") if l.strip()]
            assert len(lines) > 0, "evidence_index.jsonl is empty"
            for line in lines:
                rec = json.loads(line)
                ev = rec["evidence"]
                assert ev["evidence_id"].startswith("ev_")
                assert len(ev["content_sha256"]) == 64
                assert ev["source_pdf_sha256"]
                assert ev["parse_attempt_id"]

            # validation_report.json must exist
            vr_path = Path("runs/test_run/paper/validation/paper_validation_report.json")
            assert vr_path.exists(), "validation_report.json missing"
            vr = json.loads(vr_path.read_text())
            assert "valid" in vr

            # research_context_draft.json must exist
            ctx_path = Path("runs/test_run/context/research_context_draft.json")
            assert ctx_path.exists(), "research_context_draft.json missing"
            ctx = json.loads(ctx_path.read_text())
            assert "facts" in ctx

            # context_readiness_report.json must exist
            cr_path = Path("runs/test_run/context/context_readiness_report.json")
            assert cr_path.exists(), "context_readiness_report.json missing"

        _chdir_tmp(tmp_path, work)

    def test_e2e_fails_on_non_pdf(self, tmp_path):
        from autoad_researcher.paper_intelligence.orchestrator import PaperIntelligenceOrchestrator
        from autoad_researcher.paper_intelligence.models import PaperIntelligenceRequest
        from autoad_researcher.paper_intelligence.agent import budget_for_profile

        def work():
            txt = Path("fake.pdf")
            txt.write_text("not a real PDF")
            budget = budget_for_profile("standard")
            req = PaperIntelligenceRequest(
                schema_version=1, request_id="req_t", run_id="test_run",
                user_goal="Test",
                paper_pdf_path="fake.pdf",
                parser_profile_id="mineru_pipeline_v1",
                web_context_allowed=False, alpha_xiv_allowed=False,
                user_confirmation_policy="never",
                budget_profile="standard", budget=budget,
            )
            orch = PaperIntelligenceOrchestrator(Path("runs"))
            result = orch.run(req)
            assert result["status"] == "failed"
            assert result["stage"] == "source_attestation"

        _chdir_tmp(tmp_path, work)

    def test_rerun_same_run_id_is_blocked(self, tmp_path):
        """Same run_id with existing evidence must fail closed."""
        from autoad_researcher.paper_intelligence.orchestrator import PaperIntelligenceOrchestrator
        from autoad_researcher.paper_intelligence.models import PaperIntelligenceRequest
        from autoad_researcher.paper_intelligence.agent import budget_for_profile

        def work():
            pdf = Path("test.pdf")
            _make_valid_pdf(pdf)
            budget = budget_for_profile("standard")
            req = PaperIntelligenceRequest(
                schema_version=1, request_id="req_t", run_id="test_rerun",
                user_goal="Test", paper_pdf_path="test.pdf",
                parser_profile_id="mineru_pipeline_v1",
                web_context_allowed=False, alpha_xiv_allowed=False,
                user_confirmation_policy="never",
                budget_profile="standard", budget=budget,
            )
            orch = PaperIntelligenceOrchestrator(Path("runs"))

            # First run succeeds
            r1 = orch.run(req)
            assert r1["status"] in ("success", "partial_success")

            # Second run on same run_id must be blocked
            r2 = orch.run(req)
            assert r2["status"] == "blocked"
            assert "RUN_ALREADY_EXISTS" in r2["error"]

        _chdir_tmp(tmp_path, work)

    def test_registered_source_multiple_parse_attempts_do_not_overwrite(self, tmp_path):
        from autoad_researcher.paper_intelligence.orchestrator import PaperIntelligenceOrchestrator
        from autoad_researcher.paper_intelligence.models import PaperIntelligenceRequest
        from autoad_researcher.paper_intelligence.agent import budget_for_profile
        from autoad_researcher.ui.sources import append_source_ref, load_source_registry

        def work():
            run_dir = Path("runs/test_attempts")
            pdf_dir = run_dir / "sources" / "src_ui"
            pdf_dir.mkdir(parents=True)
            pdf = pdf_dir / "test.pdf"
            _make_valid_pdf(pdf)
            append_source_ref(
                run_dir,
                source_id="src_ui",
                kind="paper_pdf",
                user_label="test.pdf",
                stored_path="sources/src_ui/test.pdf",
                status="uploaded_not_parsed",
            )
            budget = budget_for_profile("standard")
            req = PaperIntelligenceRequest(
                schema_version=1, request_id="req_t", run_id="test_attempts",
                user_goal="Test", paper_pdf_path=str(pdf),
                parser_profile_id="mineru_pipeline_v1",
                web_context_allowed=False, alpha_xiv_allowed=False,
                user_confirmation_policy="never",
                budget_profile="standard", budget=budget,
            )
            orch = PaperIntelligenceOrchestrator(Path("runs"))

            r1 = orch.run(req)
            r2 = orch.run(req)

            assert r1["parse_attempt_id"] == "pa_000001"
            assert r2["parse_attempt_id"] == "pa_000002"
            assert Path("runs/test_attempts/paper/parse/attempts/pa_000001/parse_quality_report.json").exists()
            assert Path("runs/test_attempts/paper/parse/attempts/pa_000002/parse_quality_report.json").exists()
            quality = json.loads(Path("runs/test_attempts/paper/parse/attempts/pa_000002/parse_quality_report.json").read_text())
            assert quality["parse_attempt_id"] == "pa_000002"
            assert quality["source_id"] == "src_ui"
            assert quality["parser"] == "mineru_pipeline_v1"
            assert quality["quality_level"] == "usable"
            assert quality["usable_for"] == ["paper_artifact_synthesis", "research_context_draft"]
            assert quality["not_usable_for"] == []
            active_quality = json.loads(Path("runs/test_attempts/paper/parse/parse_quality_report.json").read_text())
            assert active_quality["parse_attempt_id"] == "pa_000002"
            reg = load_source_registry(run_dir)
            attempts = reg["sources"][0]["parse_attempts"]
            assert [attempt["parse_attempt_id"] for attempt in attempts] == ["pa_000001", "pa_000002"]

        _chdir_tmp(tmp_path, work)

    def test_parse_attempt_id_collision_rejected(self, tmp_path):
        from autoad_researcher.paper_intelligence.orchestrator import PaperIntelligenceOrchestrator
        from autoad_researcher.paper_intelligence.models import PaperIntelligenceRequest
        from autoad_researcher.paper_intelligence.agent import budget_for_profile
        from autoad_researcher.ui.sources import append_source_ref

        def work():
            run_dir = Path("runs/test_locked")
            pdf_dir = run_dir / "sources" / "src_ui"
            pdf_dir.mkdir(parents=True)
            pdf = pdf_dir / "test.pdf"
            _make_valid_pdf(pdf)
            append_source_ref(
                run_dir,
                source_id="src_ui",
                kind="paper_pdf",
                user_label="test.pdf",
                stored_path="sources/src_ui/test.pdf",
                status="uploaded_not_parsed",
            )
            (run_dir / "sources" / ".parse.lock").write_text("locked\n", encoding="utf-8")
            budget = budget_for_profile("standard")
            req = PaperIntelligenceRequest(
                schema_version=1, request_id="req_t", run_id="test_locked",
                user_goal="Test", paper_pdf_path=str(pdf),
                parser_profile_id="mineru_pipeline_v1",
                web_context_allowed=False, alpha_xiv_allowed=False,
                user_confirmation_policy="never",
                budget_profile="standard", budget=budget,
            )

            result = PaperIntelligenceOrchestrator(Path("runs")).run(req)

            assert result["status"] == "blocked"
            assert result["stage"] == "parse_attempt"
            assert result["error"] == "parse attempt in progress for this run, retry after completion"

        _chdir_tmp(tmp_path, work)

    def test_ready_branch_writes_stable_context_and_handoff(self, tmp_path):
        """emit_context_artifacts must write stable context and handoff
        when readiness is ready_for_idea_transfer_design."""
        from autoad_researcher.paper_intelligence.orchestrator import emit_context_artifacts
        from autoad_researcher.research_context.models import (
            ContextReadiness, TaskContext, ContextFact,
        )
        import json

        def work():
            ctx_dir = Path("runs/test_ready/context")
            readiness = ContextReadiness(
                status="ready_for_idea_transfer_design",
                next_stage="3.4_idea_transfer_design",
            )
            task = TaskContext(task_id="t_ready", goal="test")
            facts = [ContextFact(
                fact_id="f1", fact_type="paper_fact",
                subject="baseline", predicate="is", value="PatchCore",
                status="confirmed", producer_stage="3.2",
            )]

            paths = emit_context_artifacts(
                run_id="test_ready", task=task, source_id="src_t",
                facts=facts, gaps=[], conflicts=[],
                readiness=readiness, evidence_index_path="ev.jsonl",
                context_dir=ctx_dir,
            )

            assert paths["stable_path"] is not None
            assert paths["handoff_path"] is not None
            assert Path(paths["stable_path"]).exists()
            assert Path(paths["handoff_path"]).exists()

            ho = json.loads(Path(paths["handoff_path"]).read_text())
            assert ho["schema_version"] == 1
            assert ho["context_id"].startswith("ctx_")
            assert ho["readiness"]["status"] == "ready_for_idea_transfer_design"

        _chdir_tmp(tmp_path, work)

    def test_non_ready_branch_has_null_handoff_paths(self, tmp_path):
        """emit_context_artifacts must return None paths and not write
        stable/handoff when readiness is NOT ready_for_idea_transfer_design."""
        from autoad_researcher.paper_intelligence.orchestrator import emit_context_artifacts
        from autoad_researcher.research_context.models import (
            ContextReadiness, TaskContext,
        )

        def work():
            ctx_dir = Path("runs/test_nonready/context")
            readiness = ContextReadiness(
                status="needs_clarification",
                next_stage="3.3_context_repair",
            )
            task = TaskContext(task_id="t_nr", goal="test")

            paths = emit_context_artifacts(
                run_id="test_nonready", task=task, source_id="src_t",
                facts=[], gaps=[], conflicts=[],
                readiness=readiness, evidence_index_path=None,
                context_dir=ctx_dir,
            )

            assert paths["stable_path"] is None
            assert paths["handoff_path"] is None
            assert (ctx_dir / "research_context_draft.json").exists()
            assert (ctx_dir / "context_readiness_report.json").exists()
            assert not (ctx_dir / "research_context.json").exists()
            assert not (ctx_dir / "idea_transfer_handoff.json").exists()

        _chdir_tmp(tmp_path, work)
