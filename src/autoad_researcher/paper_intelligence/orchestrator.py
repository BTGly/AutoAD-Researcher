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
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
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
    ParseQualityReport,
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
from autoad_researcher.research_context.models import (
    EvidenceBoundary,
    IdeaTransferHandoff,
    ResearchContext,
    SourceContext,
    SourceEvidenceRef,
    TaskContext,
)
from autoad_researcher.ui.sources import (
    append_source_parse_attempt,
    load_source_registry,
    update_source_parse_attempt,
    update_source_status,
)


@dataclass(frozen=True)
class ParseAttemptHandle:
    parse_attempt_id: str
    source_id: str
    attempt_dir: Path
    lock_path: Path | None
    lock_fd: int | None
    registry_source_id: str | None


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
        Fail-closed: returns success only when post-repair validation has 0 errors.
        Reject re-run: same run_id returns RUN_ALREADY_EXISTS if artifacts exist.
        """
        run_dir = self.runs_root / request.run_id
        paper_dir = run_dir / "paper"
        registry_source_id = _find_registry_source_id(run_dir, request.paper_pdf_path)

        # Guard: reject re-run on same run_id
        if registry_source_id is None and (paper_dir / "evidence_index.jsonl").exists():
            return {
                "status": "blocked",
                "stage": "preflight",
                "error": "RUN_ALREADY_EXISTS: run_id already has evidence artifacts",
                "run_id": request.run_id,
            }

        source_dir = paper_dir / "source"
        parse_dir = paper_dir / "parse"
        artifacts_dir = paper_dir / "artifacts"
        validation_dir = paper_dir / "validation"
        context_dir = run_dir / "context"
        attempt: ParseAttemptHandle | None = None

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
            source_id=registry_source_id or f"src_{request.run_id}",
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

        try:
            attempt = _ensure_parse_attempt(run_dir, source.source_id, request.parser_profile_id)
        except RuntimeError as exc:
            return {
                "status": "blocked",
                "stage": "parse_attempt",
                "error": str(exc),
                "run_id": request.run_id,
            }

        # ============================================================
        # 2. MinerU Parse
        # ============================================================
        try:
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

            parse_result = provider.parse(parse_req, attempt.attempt_dir)
            parse_result = parse_result.model_copy(
                update={
                    "parse_attempt_id": attempt.parse_attempt_id,
                    "parser_manifest_path": str(attempt.attempt_dir / "parser_manifest.json"),
                    "canonical_output_path": str(attempt.attempt_dir),
                    "parse_quality_report_path": str(attempt.attempt_dir / "parse_quality_report.json"),
                }
            )
            if parse_result.status == "failed":
                quality = _quality_report_for_attempt(
                    provider.get_quality_report(parse_result),
                    parse_result=parse_result,
                    parser=request.parser_profile_id,
                )
                _write_atomic_json(attempt.attempt_dir / "parse_quality_report.json", quality.model_dump())
                _record_parse_attempt_result(
                    run_dir,
                    attempt,
                    status="failed",
                    warnings=parse_result.warnings,
                    make_active=False,
                )
                return {
                    "status": "parse_failed",
                    "stage": "parse",
                    "error": "; ".join(parse_result.warnings),
                    "run_id": request.run_id,
                    "parse_attempt_id": attempt.parse_attempt_id,
                }

            manifest = provider.get_manifest(parse_result)
            profile_sha = profile.compute_profile_sha256()
            _write_atomic_json(attempt.attempt_dir / "parser_manifest.json", manifest.model_dump())

            quality = _quality_report_for_attempt(
                provider.get_quality_report(parse_result),
                parse_result=parse_result,
                parser=request.parser_profile_id,
            )
            _write_atomic_json(attempt.attempt_dir / "parse_quality_report.json", quality.model_dump())

            # ============================================================
            # 3. Canonical Paper Store + Evidence Writer
            # ============================================================
            evidence_writer = EvidenceWriter(paper_dir)
            store = CanonicalPaperStore(attempt.attempt_dir)
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
                _record_parse_attempt_result(
                    run_dir,
                    attempt,
                    status="partial",
                    warnings=warnings,
                    make_active=True,
                )
                return {
                    "status": "partial_success",
                    "stage": "analysis",
                    "error": "no sections in parse output",
                    "run_id": request.run_id,
                    "parse_attempt_id": attempt.parse_attempt_id,
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
            paper_evidence_refs = _load_source_evidence_refs(evidence_writer.path)
            claims_not_supported = [
                c.claim_id
                for c in claims
                if c.status in ("confirmed", "inferred") and not c.evidence_ids
            ]
            paper_facts = [
                {"fact_id": f"f_p_{c.claim_id}", "subject": c.subject, "predicate": c.predicate,
                 "value": str(c.value), "status": c.status, "evidence_ids": c.evidence_ids,
                 "evidence_refs": _fact_source_evidence_refs(c.evidence_ids, paper_evidence_refs),
                 "producer_stage": "3.2_paper_intelligence"}
                for c in claims if c.status in ("confirmed", "inferred") and c.evidence_ids
            ]
            facts = assemble_fact_ledger(paper_facts=paper_facts)
            task = TaskContext(task_id=f"task_{request.run_id}", goal=request.user_goal)
            gaps = classify_gaps(facts, task)
            conflicts = detect_conflicts(facts)
            readiness = compute_readiness(gaps, conflicts)

            # Write context artifacts
            paths = emit_context_artifacts(
                run_id=request.run_id,
                task=task,
                source_id=source.source_id,
                facts=facts,
                gaps=gaps,
                conflicts=conflicts,
                readiness=readiness,
                evidence_index_path=str(evidence_writer.path) if evidence_writer else None,
                context_dir=context_dir,
                source_evidence=list(paper_evidence_refs.values()),
                evidence_boundary=_build_evidence_boundary(run_dir, claims_not_supported),
            )

            uc_result = build_unified_context_result(
                run_id=request.run_id,
                paper_status=final_status,
                repository_status="not_requested",
                readiness=readiness,
                draft_path=paths["draft_path"],
                report_path=paths["report_path"],
                stable_path=paths["stable_path"],
                handoff_path=paths["handoff_path"],
                warnings=warnings,
            )

            _record_parse_attempt_result(
                run_dir,
                attempt,
                status="ok" if final_status == "success" else "partial",
                warnings=warnings,
                make_active=True,
            )
            if final_status == "success":
                _sync_active_parse_snapshot(attempt.attempt_dir, parse_dir)

            return {
                "status": final_status,
                "run_id": request.run_id,
                "parse_attempt_id": attempt.parse_attempt_id,
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
        finally:
            _release_parse_lock(attempt)


def emit_context_artifacts(
    run_id: str,
    task: TaskContext,
    source_id: str,
    facts: list,
    gaps: list,
    conflicts: list,
    readiness,
    evidence_index_path: str | None,
    context_dir: Path,
    source_evidence: list[SourceEvidenceRef] | None = None,
    evidence_boundary: EvidenceBoundary | None = None,
) -> dict:
    """Write all context artifacts to disk and return their paths.

    Writes research_context_draft.json and context_readiness_report.json
    unconditionally. When readiness is ready_for_idea_transfer_design,
    also writes research_context.json and idea_transfer_handoff.json.

    Returns a dict with keys: draft_path, report_path, stable_path, handoff_path.
    stable_path and handoff_path are None when not ready.
    """
    context_dir.mkdir(parents=True, exist_ok=True)
    evidence_refs = [evidence_index_path] if evidence_index_path else []
    source_evidence = source_evidence or []
    evidence_boundary = evidence_boundary or EvidenceBoundary()

    draft_path = context_dir / "research_context_draft.json"
    draft_ctx = ResearchContext(
        schema_version=1,
        run_id=run_id,
        context_id=f"ctx_{run_id}_0",
        context_version=0,
        task=task,
        sources=SourceContext(paper_source_id=source_id),
        facts=facts,
        gaps=gaps,
        conflicts=conflicts,
        readiness=readiness,
        evidence_index_refs=evidence_refs,
        source_evidence=source_evidence,
        evidence_boundary=evidence_boundary,
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

        handoff = IdeaTransferHandoff(
            schema_version=1,
            run_id=run_id,
            context_id=stable_ctx.context_id,
            context_version=stable_ctx.context_version,
            context_sha256=stable_ctx.context_sha256,
            task_goal=task.goal,
            facts=facts,
            gaps=gaps,
            conflicts=conflicts,
            readiness=readiness,
            paper_source_id=source_id,
            evidence_index_refs=evidence_refs,
        )
        handoff_path = str(context_dir / "idea_transfer_handoff.json")
        _write_atomic_json(Path(handoff_path), handoff.model_dump())

    return {
        "draft_path": str(draft_path),
        "report_path": str(report_path),
        "stable_path": stable_path,
        "handoff_path": handoff_path,
    }


def _load_source_evidence_refs(evidence_index_path: Path) -> dict[str, SourceEvidenceRef]:
    refs: dict[str, SourceEvidenceRef] = {}
    if not evidence_index_path.is_file():
        return refs
    with open(evidence_index_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            evidence = record.get("evidence")
            if not isinstance(evidence, dict):
                continue
            evidence_id = evidence.get("evidence_id")
            source_id = evidence.get("source_id")
            parse_attempt_id = record.get("parse_attempt_id") or evidence.get("parse_attempt_id")
            if not (
                isinstance(evidence_id, str)
                and isinstance(source_id, str)
                and isinstance(parse_attempt_id, str)
            ):
                continue
            refs[evidence_id] = SourceEvidenceRef(
                source_id=source_id,
                parse_attempt_id=parse_attempt_id,
                artifact=f"paper/parse/attempts/{parse_attempt_id}",
                evidence_type=_source_evidence_type(evidence.get("source_kind")),
            )
    return refs


def _source_evidence_type(source_kind: Any) -> str:
    if source_kind in {"paper_text", "paper_table", "paper_figure", "paper_reference"}:
        return "parsed_full_text"
    if source_kind in {"web_page", "alpha_xiv_page", "arxiv_html"}:
        return "parsed_full_text"
    return "parsed_full_text"


def _fact_source_evidence_refs(
    evidence_ids: list[str],
    evidence_refs_by_id: dict[str, SourceEvidenceRef],
) -> list[SourceEvidenceRef]:
    refs: list[SourceEvidenceRef] = []
    seen: set[tuple[str, str, str, str]] = set()
    for evidence_id in evidence_ids:
        ref = evidence_refs_by_id.get(evidence_id)
        if ref is None:
            continue
        key = (ref.source_id, ref.parse_attempt_id, ref.artifact, ref.evidence_type)
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    return refs


def _build_evidence_boundary(run_dir: Path, claims_not_supported: list[str]) -> EvidenceBoundary:
    try:
        registry = load_source_registry(run_dir)
    except Exception:
        return EvidenceBoundary(claims_not_supported=claims_not_supported)

    unparsed_sources: list[str] = []
    partial_parse_attempts: list[str] = []
    failed_parse_attempts: list[str] = []
    for source in registry.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_id = source.get("source_id")
        attempts = source.get("parse_attempts", [])
        active_parse_attempt_id = source.get("active_parse_attempt_id")
        if (
            isinstance(source_id, str)
            and source.get("status") in {"uploaded_not_parsed", "user_provided_not_ingested", "parsing"}
            and not active_parse_attempt_id
        ):
            unparsed_sources.append(source_id)
        if not isinstance(attempts, list):
            continue
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            parse_attempt_id = attempt.get("parse_attempt_id")
            if not isinstance(parse_attempt_id, str):
                continue
            if attempt.get("status") == "failed":
                failed_parse_attempts.append(parse_attempt_id)
            elif attempt.get("status") == "partial":
                partial_parse_attempts.append(parse_attempt_id)

    return EvidenceBoundary(
        unparsed_sources=unparsed_sources,
        partial_parse_attempts=partial_parse_attempts,
        failed_parse_attempts=failed_parse_attempts,
        claims_not_supported=claims_not_supported,
    )


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


def _find_registry_source_id(run_dir: Path, paper_pdf_path: str) -> str | None:
    try:
        registry = load_source_registry(run_dir)
    except Exception:
        return None
    try:
        requested = Path(paper_pdf_path).resolve()
    except OSError:
        return None
    for source in registry.get("sources", []):
        stored_path = source.get("stored_path")
        if not isinstance(stored_path, str) or not stored_path:
            continue
        try:
            candidate = (run_dir / stored_path).resolve()
        except OSError:
            continue
        if candidate == requested:
            source_id = source.get("source_id")
            return source_id if isinstance(source_id, str) else None
    return None


def _ensure_parse_attempt(run_dir: Path, source_id: str, parser_profile_id: str) -> ParseAttemptHandle:
    lock_path, lock_fd = _acquire_parse_lock(run_dir)
    parse_attempt_id = _next_parse_attempt_id(run_dir)
    attempt_dir = run_dir / "paper" / "parse" / "attempts" / parse_attempt_id
    if attempt_dir.exists():
        _release_parse_lock(ParseAttemptHandle(parse_attempt_id, source_id, attempt_dir, lock_path, lock_fd, source_id))
        raise RuntimeError(f"parse attempt already exists: {parse_attempt_id}")
    attempt_dir.mkdir(parents=True, exist_ok=False)

    registry_source_id = _source_exists_in_registry(run_dir, source_id)
    handle = ParseAttemptHandle(
        parse_attempt_id=parse_attempt_id,
        source_id=source_id,
        attempt_dir=attempt_dir,
        lock_path=lock_path,
        lock_fd=lock_fd,
        registry_source_id=source_id if registry_source_id else None,
    )
    if handle.registry_source_id is not None:
        now = datetime.now(timezone.utc).isoformat()
        append_source_parse_attempt(
            run_dir,
            source_id,
            {
                "parse_attempt_id": parse_attempt_id,
                "source_id": source_id,
                "parser": parser_profile_id,
                "status": "running",
                "output_dir": _relative_to_run(run_dir, attempt_dir),
                "quality_report": _relative_to_run(run_dir, attempt_dir / "parse_quality_report.json"),
                "created_at": now,
            },
            make_active=False,
        )
        update_source_status(run_dir, source_id, "parsing")
    return handle


def _acquire_parse_lock(run_dir: Path) -> tuple[Path, int]:
    lock_dir = run_dir / "sources"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".parse.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("parse attempt in progress for this run, retry after completion") from exc
    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    return lock_path, fd


def _release_parse_lock(handle: ParseAttemptHandle | None) -> None:
    if handle is None:
        return
    if handle.lock_fd is not None:
        try:
            os.close(handle.lock_fd)
        except OSError:
            pass
    if handle.lock_path is not None:
        try:
            handle.lock_path.unlink()
        except FileNotFoundError:
            pass


def _next_parse_attempt_id(run_dir: Path) -> str:
    try:
        registry = load_source_registry(run_dir)
    except Exception:
        registry = {"sources": []}
    max_seen = 0
    for source in registry.get("sources", []):
        attempts = source.get("parse_attempts", [])
        if not isinstance(attempts, list):
            continue
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            attempt_id = attempt.get("parse_attempt_id")
            if not isinstance(attempt_id, str):
                continue
            if not attempt_id.startswith("pa_"):
                continue
            suffix = attempt_id[3:]
            if suffix.isdigit():
                max_seen = max(max_seen, int(suffix))
    return f"pa_{max_seen + 1:06d}"


def _source_exists_in_registry(run_dir: Path, source_id: str) -> bool:
    try:
        registry = load_source_registry(run_dir)
    except Exception:
        return False
    return any(source.get("source_id") == source_id for source in registry.get("sources", []))


def _record_parse_attempt_result(
    run_dir: Path,
    attempt: ParseAttemptHandle,
    *,
    status: str,
    warnings: list[str],
    make_active: bool,
) -> None:
    if attempt.registry_source_id is None:
        return
    update_source_parse_attempt(
        run_dir,
        attempt.source_id,
        attempt.parse_attempt_id,
        {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "warnings": list(warnings),
        },
        make_active=make_active,
    )
    if status == "ok":
        update_source_status(run_dir, attempt.source_id, "parsed")
    elif status in {"failed", "partial"}:
        update_source_status(run_dir, attempt.source_id, "failed", error_message="; ".join(warnings) or f"parse attempt {status}")


def _quality_report_for_attempt(
    quality: ParseQualityReport,
    *,
    parse_result,
    parser: str,
) -> ParseQualityReport:
    if parse_result.status == "success":
        quality_level = "usable"
        usable_for = ["paper_artifact_synthesis", "research_context_draft"]
        not_usable_for: list[str] = []
    elif parse_result.status == "partial_success":
        quality_level = "partial"
        usable_for = ["parse_diagnostics"]
        not_usable_for = ["supported_research_facts"]
    else:
        quality_level = "unusable"
        usable_for = []
        not_usable_for = ["paper_content_claims", "research_context_draft"]

    return quality.model_copy(
        update={
            "parse_attempt_id": parse_result.parse_attempt_id,
            "source_id": parse_result.source_id,
            "parser": parser,
            "quality_level": quality_level,
            "usable_for": usable_for,
            "not_usable_for": not_usable_for,
        }
    )


def _sync_active_parse_snapshot(attempt_dir: Path, parse_dir: Path) -> None:
    parse_dir.mkdir(parents=True, exist_ok=True)
    for path in attempt_dir.iterdir():
        if path.is_file():
            shutil.copy2(path, parse_dir / path.name)


def _relative_to_run(run_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(run_dir).as_posix()
    except ValueError:
        return str(path)


def _write_atomic_json(path: Path, data: Any) -> None:
    import os
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.chmod(0o644)
    os.replace(tmp, path)
