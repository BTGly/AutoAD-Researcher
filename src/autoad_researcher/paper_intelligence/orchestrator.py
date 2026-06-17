"""End-to-end Paper Intelligence orchestration.

Wires together source attestation, MinerU parse, canonical store,
analysis agent, artifact synthesis, evidence validation, and repair
into a single deterministic flow.

Evidence: paper_read and paper_search generate PaperTextEvidenceRef
with content SHA256, appended to evidence_index.jsonl. Claims and
candidates reference real evidence_ids.

Fail-closed: post-repair re-validation. If errors remain, returns
partial_success (never success).
"""

import json
from pathlib import Path
from typing import Any

from autoad_researcher.paper_intelligence.agent import (
    PaperArtifactSynthesizer,
    budget_for_profile,
)
from autoad_researcher.paper_intelligence.attestation import attest_paper_source
from autoad_researcher.paper_intelligence.errors import (
    PaperSourceError,
)
from autoad_researcher.paper_intelligence.models import (
    PaperIntelligenceRequest,
    PaperSource,
    PaperClaim,
    PaperMentionedCandidate,
)
from autoad_researcher.paper_intelligence.mineru_provider import (
    FixtureMinerUProvider,
    MINERU_PIPELINE_V1_PROFILE,
)
from autoad_researcher.paper_intelligence.parser_models import (
    DocumentParseRequest,
)
from autoad_researcher.paper_intelligence.tools import CanonicalPaperStore, EvidenceWriter
from autoad_researcher.paper_intelligence.validator import (
    PaperValidationReport,
    validate_claim,
    validate_candidate,
)
from autoad_researcher.paper_intelligence.repair import run_paper_repair
from autoad_researcher.research_context.assembly import (
    assemble_fact_ledger,
    classify_gaps,
    detect_conflicts,
    compute_readiness,
    build_unified_context_result,
)
from autoad_researcher.research_context.models import TaskContext, ResearchContext, SourceContext


class PaperIntelligenceOrchestrator:
    """End-to-end Paper Intelligence flow orchestrator.

    Deterministic, no real LLM calls. Uses fixture MinerU provider
    and canonical paper store for evidence-backed analysis.
    Fail-closed: returns success only when post-repair validation has 0 errors.
    """

    def __init__(self, runs_root: Path = Path("runs")):
        self.runs_root = Path(runs_root)

    def run(self, request: PaperIntelligenceRequest) -> dict:
        """Execute the full Paper Intelligence pipeline.

        Returns a dict with status, statistics, and paths.
        """
        run_dir = self.runs_root / request.run_id
        paper_dir = run_dir / "paper"
        source_dir = paper_dir / "source"
        parse_dir = paper_dir / "parse"
        artifacts_dir = paper_dir / "artifacts"
        validation_dir = paper_dir / "validation"
        context_dir = run_dir / "context"

        warnings: list[str] = []

        # ============================================================
        # 1. Source Attestation
        # ============================================================
        try:
            source_attrs = attest_paper_source(request.paper_pdf_path, request.paper_pdf_path)
        except PaperSourceError as e:
            return {
                "status": "failed",
                "stage": "source_attestation",
                "error": str(e),
                "run_id": request.run_id,
            }

        source = PaperSource(
            schema_version=1,
            source_id=f"src_{request.run_id}",
            source_kind="user_pdf",
            original_filename_label=request.paper_pdf_path,
            storage_path_label=request.paper_pdf_path,
            source_pdf_sha256=source_attrs["source_pdf_sha256"],
            size_bytes=source_attrs["size_bytes"],
            page_count=source_attrs["page_count"],
            mime_type=source_attrs["mime_type"],
            created_at=source_attrs["created_at"],
        )

        source_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic_json(source_dir / "paper_source.json", source.model_dump())

        # ============================================================
        # 2. MinerU Parse
        # ============================================================
        profile = MINERU_PIPELINE_V1_PROFILE
        provider = FixtureMinerUProvider(
            profile=profile,
            runtime_python_version="3.10",
            runtime_platform="linux",
            device_profile="cpu",
        )

        parse_req = DocumentParseRequest(
            schema_version=1,
            source_id=source.source_id,
            source_pdf_path=request.paper_pdf_path,
            parser_profile_id=request.parser_profile_id,
            ocr_policy="auto",
            language_hints=[],
            max_pages=40,
            max_runtime_seconds=600,
        )

        parse_result = provider.parse(parse_req, parse_dir)
        if parse_result.status == "failed":
            return {
                "status": "parse_failed",
                "stage": "parse",
                "error": "; ".join(parse_result.warnings),
                "run_id": request.run_id,
            }

        manifest = provider.get_manifest(parse_result)
        profile_sha = profile.compute_profile_sha256()
        _write_atomic_json(parse_dir / "parser_manifest.json", manifest.model_dump())

        quality = provider.get_quality_report(parse_result)
        _write_atomic_json(parse_dir / "parse_quality_report.json", quality.model_dump())

        # ============================================================
        # 3. Canonical Paper Store + Evidence Writer
        # ============================================================
        evidence_writer = EvidenceWriter(paper_dir)
        store = CanonicalPaperStore(parse_dir)
        store.set_source_identity(
            source_id=source.source_id,
            source_pdf_sha256=source.source_pdf_sha256,
            parse_attempt_id=parse_result.parse_attempt_id,
            parser_profile_sha256=profile_sha,
            canonical_output_sha256=manifest.canonical_output_sha256,
        )
        store.set_evidence_writer(evidence_writer)
        sections = store.list_sections()
        if not sections:
            warnings.append("parse produced no sections")
            return {
                "status": "partial_success",
                "stage": "analysis",
                "error": "no sections in parse output",
                "run_id": request.run_id,
                "warnings": warnings,
            }

        # ============================================================
        # 4. Analysis: extract claims with real evidence_ids
        # ============================================================
        budget = request.budget or budget_for_profile(request.budget_profile)
        claims, candidates = _analyze_paper_content(store, sections, budget)

        evidence_ref_count = sum(len(c.evidence_ids) for c in claims)

        # ============================================================
        # 5. Initial Synthesis
        # ============================================================
        synthesizer = PaperArtifactSynthesizer(request.run_id, source.source_id, artifacts_dir)
        synthesizer.synthesize(
            claims=claims,
            candidates=candidates,
            components=[],
            idea_sources=[],
            repo_links=[],
            status="success",
            warnings=warnings,
        )

        # ============================================================
        # 6. Validation
        # ============================================================
        validation_dir.mkdir(parents=True, exist_ok=True)
        claim_issues = []
        for c in claims:
            claim_issues.extend(validate_claim(c))
        cand_issues = []
        for c in candidates:
            cand_issues.extend(validate_candidate(c))

        initial_valid = len(claim_issues) == 0 and len(cand_issues) == 0

        # ============================================================
        # 7. Repair if needed
        # ============================================================
        repairs_used = 0
        if not initial_valid:
            repaired_claims, repaired_candidates, repairs_used = run_paper_repair(
                claims, candidates,
                PaperValidationReport(valid=False, claim_issues=claim_issues, candidate_issues=cand_issues),
                budget,
            )
            claims = repaired_claims
            candidates = repaired_candidates

        # ============================================================
        # 8. Post-repair validation
        # ============================================================
        post_claim_issues = []
        for c in claims:
            post_claim_issues.extend(validate_claim(c))
        post_cand_issues = []
        for c in candidates:
            post_cand_issues.extend(validate_candidate(c))

        post_error_count = len(post_claim_issues) + len(post_cand_issues)
        post_valid = post_error_count == 0

        report = PaperValidationReport(
            valid=post_valid,
            claim_issues=post_claim_issues,
            candidate_issues=post_cand_issues,
        )
        _write_atomic_json(validation_dir / "paper_validation_report.json", {
            "valid": report.valid,
            "error_count": report.error_count,
            "claim_issues": [{"claim_id": i.claim_id, "issue": i.issue, "severity": i.severity} for i in report.claim_issues],
            "candidate_issues": [{"candidate_id": i.candidate_id, "issue": i.issue, "severity": i.severity} for i in report.candidate_issues],
        })

        # ============================================================
        # 9. Final synthesis
        # ============================================================
        validated_claims = [c for c in claims if c.status == "confirmed" and c.evidence_ids]
        unsupported_claims = [c for c in claims if c.status == "confirmed" and not c.evidence_ids]

        final_status = "success" if (post_valid and not warnings) else "partial_success"
        if post_error_count > 0:
            warnings.append(f"post-repair validation has {post_error_count} remaining errors")
        if unsupported_claims:
            warnings.append(f"{len(unsupported_claims)} confirmed claims lack evidence")

        synth = synthesizer.synthesize(
            claims=claims,
            candidates=candidates,
            components=[],
            idea_sources=[],
            repo_links=[],
            status=final_status,
            warnings=warnings,
        )

        # ============================================================
        # 10. Unified Research Context
        # ============================================================
        paper_facts = [
            {"fact_id": f"f_p_{c.claim_id}", "subject": c.subject, "predicate": c.predicate,
             "value": str(c.value), "status": c.status, "evidence_ids": c.evidence_ids,
             "producer_stage": "3.2_paper_intelligence"}
            for c in claims if c.status in ("confirmed", "inferred") and c.evidence_ids
        ]
        facts = assemble_fact_ledger(paper_facts=paper_facts)
        task = TaskContext(task_id=f"task_{request.run_id}", goal=request.user_goal)
        gaps = classify_gaps(facts, task)
        conflicts = detect_conflicts(facts)
        readiness = compute_readiness(gaps, conflicts)

        # Write context artifacts
        context_dir.mkdir(parents=True, exist_ok=True)
        draft_path = context_dir / "research_context_draft.json"
        draft_ctx = ResearchContext(
            schema_version=1,
            run_id=request.run_id,
            context_id=f"ctx_{request.run_id}_0",
            context_version=0,
            task=task,
            sources=SourceContext(paper_source_id=source.source_id),
            facts=facts,
            gaps=gaps,
            conflicts=conflicts,
            readiness=readiness,
            evidence_index_refs=[str(evidence_writer.path)] if evidence_writer else [],
            context_sha256="0" * 64,
        )
        _write_atomic_json(draft_path, draft_ctx.model_dump())

        report_path = context_dir / "context_readiness_report.json"
        _write_atomic_json(report_path, readiness.model_dump())

        stable_path: str | None = None
        handoff_path: str | None = None
        if readiness.status == "ready_for_idea_transfer_design":
            from autoad_researcher.research_context.assembly import finalize_research_context
            stable_ctx = finalize_research_context(draft_ctx, readiness)
            stable_path = str(context_dir / "research_context.json")
            _write_atomic_json(Path(stable_path), stable_ctx.model_dump())

            handoff = {
                "schema_version": 1,
                "run_id": request.run_id,
                "context_id": stable_ctx.context_id,
                "context_version": stable_ctx.context_version,
                "context_sha256": stable_ctx.context_sha256,
                "task_goal": task.goal,
                "facts": [f.model_dump() for f in facts],
                "gaps": [g.model_dump() for g in gaps],
                "conflicts": [c.model_dump() for c in conflicts],
                "readiness": readiness.model_dump(),
                "paper_source_id": source.source_id,
                "evidence_index_path": str(evidence_writer.path) if evidence_writer else None,
            }
            handoff_path = str(context_dir / "idea_transfer_handoff.json")
            _write_atomic_json(Path(handoff_path), handoff)

        uc_result = build_unified_context_result(
            run_id=request.run_id,
            paper_status=final_status,
            repository_status="not_requested",
            readiness=readiness,
            draft_path=str(draft_path),
            report_path=str(report_path),
            stable_path=stable_path,
            handoff_path=handoff_path,
            warnings=warnings,
        )

        return {
            "status": final_status,
            "run_id": request.run_id,
            "claim_count": len(claims),
            "evidence_ref_count": evidence_ref_count,
            "validated_claim_count": len(validated_claims),
            "unsupported_claim_count": len(unsupported_claims),
            "candidate_count": len(candidates),
            "repairs_used": repairs_used,
            "post_validation_errors": post_error_count,
            "paper_reader_result": synth.paper_reader_result.model_dump(),
            "context_result": uc_result.model_dump(),
            "warnings": warnings,
        }


def _analyze_paper_content(
    store: CanonicalPaperStore,
    sections: list,
    budget,
) -> tuple[list[PaperClaim], list[PaperMentionedCandidate]]:
    """Analyze paper content through the canonical paper store.

    Reads sections, searches for key terms, and produces evidence-backed
    claims and candidates. Every confirmed claim gets real evidence_ids
    from the store's search/read operations.
    """
    claims: list[PaperClaim] = []
    candidates: list[PaperMentionedCandidate] = []
    claim_idx = 0

    first_section = sections[0] if sections else None
    first_text = ""
    if first_section:
        block_ids = getattr(first_section, "block_ids", [])
        try:
            results = store.read_blocks(block_ids)
            if results:
                first_text = results[0].content
                # Title claim with real evidence from reading the block
                claims.append(PaperClaim(
                    claim_id=f"cl_{claim_idx}", subject="title", predicate="is",
                    value=first_section.title if first_section else "Untitled",
                    status="confirmed", confidence="medium",
                    evidence_ids=[results[0].evidence.evidence_id],
                ))
                claim_idx += 1
        except Exception:
            pass

    if not first_text and first_section:
        claims.append(PaperClaim(
            claim_id=f"cl_{claim_idx}", subject="title", predicate="is",
            value=first_section.title if first_section else "Untitled",
            status="inferred", confidence="low",
            rationale_summary="title from section header",
            evidence_ids=[],
        ))
        claim_idx += 1

    # Search for method terms — each search result produces evidence
    method_terms = ["propose", "method", "approach", "network", "model", "architecture",
                    "training", "loss", "anomaly", "detection", "patch", "feature"]
    for term in method_terms:
        if claim_idx >= budget.max_analysis_reads:
            break
        search_results = store.search(term, max_results=3)
        if search_results:
            ev_ids = [r.evidence.evidence_id for r in search_results]
            claims.append(PaperClaim(
                claim_id=f"cl_{claim_idx}", subject="proposed_method",
                predicate="mentions", value=term,
                status="confirmed", confidence="medium",
                evidence_ids=ev_ids,
            ))
            claim_idx += 1

    # Search for baseline candidates
    baseline_terms = ["resnet", "patchcore", "padim", "fastflow", "efficientad", "draem",
                      "wide", "vgg", "vit", "swin"]
    for term in baseline_terms:
        search_results = store.search(term, max_results=1)
        if search_results:
            ev_ids = [r.evidence.evidence_id for r in search_results]
            candidates.append(PaperMentionedCandidate(
                candidate_id=f"cand_{term}",
                kind="baseline",
                name=term.upper(),
                mention_role="compared_baseline" if term != "patchcore" else "proposed_method",
                selection_status="paper_mentioned",
                evidence_ids=ev_ids,
            ))

    dataset_terms = ["mvtec", "cifar", "imagenet", "coco", "visa", "btad"]
    for term in dataset_terms:
        search_results = store.search(term, max_results=1)
        if search_results:
            ev_ids = [r.evidence.evidence_id for r in search_results]
            candidates.append(PaperMentionedCandidate(
                candidate_id=f"cand_ds_{term}",
                kind="dataset",
                name=term.upper(),
                mention_role="dataset_evaluation",
                selection_status="paper_mentioned",
                evidence_ids=ev_ids,
            ))

    return claims, candidates


def _write_atomic_json(path: Path, data: Any) -> None:
    import os
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.chmod(0o644)
    os.replace(tmp, path)
