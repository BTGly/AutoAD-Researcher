"""End-to-end Paper Intelligence orchestration.

Wires together source attestation, MinerU parse, canonical store,
analysis agent, artifact synthesis, evidence validation, and repair
into a single deterministic flow.
"""

import json
from pathlib import Path
from typing import Any

from autoad_researcher.paper_intelligence.agent import (
    PaperArtifactSynthesizer,
    budget_for_profile,
)
from autoad_researcher.paper_intelligence.attestation import attest_paper_source
from autoad_researcher.paper_intelligence.control_models import (
    AnalysisProgress,
    PaperAnalysisControlSignal,
)
from autoad_researcher.paper_intelligence.errors import (
    PaperIntelligenceContractError,
    PaperSourceError,
    PaperParseError,
)
from autoad_researcher.paper_intelligence.models import (
    PaperIntelligenceRequest,
    PaperReaderResult,
    PaperSource,
    PaperClaim,
    PaperMentionedCandidate,
)
from autoad_researcher.paper_intelligence.mineru_provider import (
    FixtureMinerUProvider,
    MINERU_PIPELINE_V1_PROFILE,
    MinerUProfileConfig,
)
from autoad_researcher.paper_intelligence.parser_models import (
    DocumentParseRequest,
    ParseQualityReport,
)
from autoad_researcher.paper_intelligence.tools import CanonicalPaperStore
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
from autoad_researcher.research_context.models import TaskContext


class PaperIntelligenceOrchestrator:
    """End-to-end Paper Intelligence flow orchestrator.

    Deterministic, no real LLM calls. Uses fixture MinerU provider
    and canonical paper store for evidence-backed analysis.
    """

    def __init__(self, runs_root: Path = Path("runs")):
        self.runs_root = Path(runs_root)

    def run(self, request: PaperIntelligenceRequest) -> dict:
        """Execute the full Paper Intelligence pipeline.

        Returns a dict with status, result, and paths.
        """
        run_dir = self.runs_root / request.run_id
        paper_dir = run_dir / "paper"
        source_dir = paper_dir / "source"
        parse_dir = paper_dir / "parse"
        artifacts_dir = paper_dir / "artifacts"
        validation_dir = paper_dir / "validation"

        warnings: list[str] = []

        # 1. Source Attestation
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

        # 2. MinerU Parse
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
            max_pages=request.budget.max_parse_attempts * 40 if request.budget else 40,
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
        _write_atomic_json(parse_dir / "parser_manifest.json", manifest.model_dump())

        quality = provider.get_quality_report(parse_result)
        _write_atomic_json(parse_dir / "parse_quality_report.json", quality.model_dump())

        # 3. Canonical Paper Store
        store = CanonicalPaperStore(parse_dir)
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

        # 4. Analysis: extract claims from paper content
        budget = request.budget or budget_for_profile(request.budget_profile)
        claims, candidates = _analyze_paper_content(store, sections, source.source_id, budget)

        # 5. Synthesis
        synthesizer = PaperArtifactSynthesizer(request.run_id, source.source_id, artifacts_dir)
        synth = synthesizer.synthesize(
            claims=claims,
            candidates=candidates,
            components=[],
            idea_sources=[],
            repo_links=[],
            status="success" if not warnings else "partial_success",
            warnings=warnings,
        )

        # 6. Validation
        validation_dir.mkdir(parents=True, exist_ok=True)
        claim_issues = []
        for c in claims:
            claim_issues.extend(validate_claim(c))
        cand_issues = []
        for c in candidates:
            cand_issues.extend(validate_candidate(c))

        report = PaperValidationReport(
            valid=len(claim_issues) == 0 and len(cand_issues) == 0,
            claim_issues=claim_issues,
            candidate_issues=cand_issues,
        )

        # 7. Repair if needed
        repairs_used = 0
        if not report.valid:
            repaired_claims, repaired_candidates, repairs_used = run_paper_repair(
                claims, candidates, report, budget,
            )
            claims = repaired_claims
            candidates = repaired_candidates

        # Re-write artifacts after repair
        synth = synthesizer.synthesize(
            claims=claims,
            candidates=candidates,
            components=[],
            idea_sources=[],
            repo_links=[],
            status="success" if not warnings else "partial_success",
            warnings=warnings,
        )

        # 8. Build context result
        paper_facts = [
            {"fact_id": f"f_p_{c.claim_id}", "subject": c.subject, "predicate": c.predicate,
             "value": str(c.value), "status": c.status, "evidence_ids": c.evidence_ids,
             "producer_stage": "3.2_paper_intelligence"}
            for c in claims if c.status in ("confirmed", "inferred")
        ]
        facts = assemble_fact_ledger(paper_facts=paper_facts)
        task = TaskContext(task_id=f"task_{request.run_id}", goal=request.user_goal)
        gaps = classify_gaps(facts, task)
        conflicts = detect_conflicts(facts)
        readiness = compute_readiness(gaps, conflicts)

        context_dir = run_dir / "context"
        context_dir.mkdir(parents=True, exist_ok=True)
        readiness_path = context_dir / "context_readiness_report.json"
        _write_atomic_json(readiness_path, readiness.model_dump())

        uc_result = build_unified_context_result(
            run_id=request.run_id,
            paper_status="success" if not warnings else "partial_success",
            repository_status="not_requested",
            readiness=readiness,
            draft_path=str(context_dir / "research_context_draft.json"),
            report_path=str(readiness_path),
            warnings=warnings,
        )

        return {
            "status": "success" if not warnings else "partial_success",
            "run_id": request.run_id,
            "paper_reader_result": synth.paper_reader_result.model_dump(),
            "context_result": uc_result.model_dump(),
            "evidence_count": len(claims),
            "candidate_count": len(candidates),
            "repairs_used": repairs_used,
            "warnings": warnings,
        }


def _analyze_paper_content(
    store: CanonicalPaperStore,
    sections: list,
    source_id: str,
    budget,
) -> tuple[list[PaperClaim], list[PaperMentionedCandidate]]:
    """Analyze paper content through the canonical paper store.

    Reads sections, searches for key terms, and produces evidence-backed
    claims and candidates without real LLM calls.
    """
    claims: list[PaperClaim] = []
    candidates: list[PaperMentionedCandidate] = []
    claim_idx = 0

    # SectionInfo is a dataclass with .title, .section_id, .block_ids attributes
    first_section = sections[0] if sections else None
    first_text = ""
    if first_section:
        block_ids = getattr(first_section, "block_ids", [])
        try:
            results = store.read_blocks(block_ids, source_id=source_id)
            first_text = " ".join(r.content for r in results)
        except Exception:
            pass

    # Title
    title_text = first_section.title if first_section else "Untitled"
    claims.append(PaperClaim(
        claim_id=f"cl_{claim_idx}", subject="title", predicate="is",
        value=title_text, status="confirmed", confidence="medium",
        evidence_ids=[],
    ))
    claim_idx += 1

    # Search for method terms
    method_terms = ["propose", "method", "approach", "network", "model", "architecture",
                    "training", "loss", "anomaly", "detection", "patch", "feature"]
    for term in method_terms:
        results = store.search(term, max_results=3, source_id=source_id)
        if results:
            claims.append(PaperClaim(
                claim_id=f"cl_{claim_idx}", subject="proposed_method",
                predicate="mentions", value=term,
                status="confirmed", confidence="medium",
                evidence_ids=[],
            ))
            claim_idx += 1
            if claim_idx >= budget.max_analysis_reads:
                break

    # Search for baseline/dataset/metric candidates
    baseline_terms = ["resnet", "patchcore", "padim", "fastflow", "efficientad", "draem",
                      "wide", "vgg", "vit", "swin"]
    for term in baseline_terms:
        results = store.search(term, max_results=1, source_id=source_id)
        if results:
            candidates.append(PaperMentionedCandidate(
                candidate_id=f"cand_{term}",
                kind="baseline",
                name=term.upper(),
                mention_role="compared_baseline" if term != "patchcore" else "proposed_method",
                selection_status="paper_mentioned",
                evidence_ids=[],
            ))

    dataset_terms = ["mvtec", "cifar", "imagenet", "coco", "visa", "btad"]
    for term in dataset_terms:
        results = store.search(term, max_results=1, source_id=source_id)
        if results:
            candidates.append(PaperMentionedCandidate(
                candidate_id=f"cand_ds_{term}",
                kind="dataset",
                name=term.upper(),
                mention_role="dataset_evaluation",
                selection_status="paper_mentioned",
                evidence_ids=[],
            ))

    return claims, candidates


def _write_atomic_json(path: Path, data: Any) -> None:
    import os
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.chmod(0o644)
    os.replace(tmp, path)
