"""Unified Research Context assembly, validation, and handoff (U2-U7).

U2: Context Assembly — combine paper, repo, user, policy facts into fact ledger
U3: Gap/Conflict Classification — classify gaps by type and resolution target
U4: Context Readiness Validator — determine if context is ready for 3.4
U5: 3.3 Trigger Protocol — conditionally trigger 3.3 for user-decision gaps
U6: Reader Reanalysis Requests — route evidence gaps back to capabilities
U7: Stable Context and 3.4 Handoff — finalize context for downstream
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from autoad_researcher.research_context.models import (
    CandidateDecision,
    ContextConflict,
    ContextFact,
    ContextReadiness,
    InformationGap,
    ResearchContext,
    TaskContext,
    SourceContext,
    UnifiedResearchContextResult,
)


# ---------------------------------------------------------------------------
# U2: Context Assembly
# ---------------------------------------------------------------------------


def assemble_fact_ledger(
    paper_facts: list[dict[str, Any]] | None = None,
    repository_facts: list[dict[str, Any]] | None = None,
    user_facts: list[dict[str, Any]] | None = None,
    environment_facts: list[dict[str, Any]] | None = None,
    policy_facts: list[dict[str, Any]] | None = None,
) -> list[ContextFact]:
    """Combine facts from multiple sources into a unified fact ledger.

    Facts from different sources are never silently merged or overwritten.
    Duplicate fact_ids are rejected.
    """
    facts: list[ContextFact] = []
    seen_ids: set[str] = set()

    sources = [
        ("paper_fact", paper_facts or []),
        ("repository_fact", repository_facts or []),
        ("user_fact", user_facts or []),
        ("environment_fact", environment_facts or []),
        ("system_policy_fact", policy_facts or []),
    ]

    for fact_type, fact_list in sources:
        for f in fact_list:
            fid = f.get("fact_id", "")
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            facts.append(ContextFact(
                fact_id=fid,
                fact_type=fact_type,
                subject=f.get("subject", ""),
                predicate=f.get("predicate", ""),
                value=f.get("value", ""),
                status=f.get("status", "confirmed"),
                evidence_ids=f.get("evidence_ids", []),
                producer_stage=f.get("producer_stage", "3.2"),
            ))

    return facts


# ---------------------------------------------------------------------------
# U3: Gap Classification
# ---------------------------------------------------------------------------


def classify_gaps(
    facts: list[ContextFact],
    task: TaskContext,
    paper_candidates: list[dict[str, Any]] | None = None,
) -> list[InformationGap]:
    """Classify gaps in the research context.

    Gaps are classified by type:
    - user_decision_required → needs user input (triggers 3.3)
    - paper_evidence_required → needs Paper Capability re-run
    - repository_evidence_required → needs Repository Capability re-run
    """
    gaps: list[InformationGap] = []

    # Check for missing baseline selection
    has_baseline = any(
        f.subject == "baseline" and f.status == "confirmed" for f in facts
    )
    if not has_baseline and any(
        f.subject == "baseline_candidate" for f in facts
    ):
        gaps.append(InformationGap(
            gap_id="gap_baseline",
            gap_type="user_decision_required",
            category="baseline_selection",
            severity="blocking",
            question_needed=True,
            reason="No baseline has been selected; candidates exist in paper",
            downstream_impact="Cannot proceed to experiment design without a selected baseline",
            resolution_target="3.3_context_repair",
        ))

    # Check for missing dataset selection
    has_dataset = any(
        f.subject == "dataset" and f.status == "confirmed" for f in facts
    )
    if not has_dataset:
        gaps.append(InformationGap(
            gap_id="gap_dataset",
            gap_type="user_decision_required",
            category="dataset_selection",
            severity="blocking",
            question_needed=True,
            reason="No dataset has been selected",
            downstream_impact="Cannot proceed to experiment design without a selected dataset",
            resolution_target="3.3_context_repair",
        ))

    # Check for missing task goal
    if not task.goal:
        gaps.append(InformationGap(
            gap_id="gap_task_goal",
            gap_type="user_decision_required",
            category="task_goal",
            severity="blocking",
            question_needed=True,
            reason="Task goal is empty",
            downstream_impact="Cannot proceed without a clear research task goal",
            resolution_target="3.3_context_repair",
        ))

    # Check for paper evidence gaps
    paper_facts = [f for f in facts if f.fact_type == "paper_fact"]
    if not paper_facts:
        gaps.append(InformationGap(
            gap_id="gap_paper_evidence",
            gap_type="paper_evidence_required",
            category="reader_reanalysis_needed",
            severity="high",
            question_needed=False,
            reason="No paper facts available for evidence-backed context",
            downstream_impact="Context may lack paper-derived claims",
            resolution_target="paper_intelligence",
        ))

    return gaps


# ---------------------------------------------------------------------------
# U3: Conflict Detection
# ---------------------------------------------------------------------------


def detect_conflicts(
    facts: list[ContextFact],
) -> list[ContextConflict]:
    """Detect conflicts between different source types.

    Only adds conflicts that meet minimum fact count (>=2).
    """
    conflicts: list[ContextConflict] = []

    # Check paper vs repository conflicts (same subject, different values)
    paper_facts = {f.subject: f for f in facts if f.fact_type == "paper_fact" and f.status == "confirmed"}
    repo_facts = {f.subject: f for f in facts if f.fact_type == "repository_fact" and f.status == "confirmed"}

    for subject in set(paper_facts) & set(repo_facts):
        if str(paper_facts[subject].value) != str(repo_facts[subject].value):
            conflicts.append(ContextConflict(
                conflict_id=f"conflict_{subject}",
                conflict_type="paper_vs_repository",
                fact_ids=[paper_facts[subject].fact_id, repo_facts[subject].fact_id],
                evidence_ids=paper_facts[subject].evidence_ids + repo_facts[subject].evidence_ids,
            ))

    return conflicts


# ---------------------------------------------------------------------------
# U4: Context Readiness Validator
# ---------------------------------------------------------------------------


def compute_readiness(
    gaps: list[InformationGap],
    conflicts: list[ContextConflict],
    decisions: list[CandidateDecision] | None = None,
) -> ContextReadiness:
    """Compute context readiness based on gaps and conflicts.

    Returns the readiness status and next stage routing.
    """
    decisions = decisions or []

    blocking_gaps = [g for g in gaps if g.severity == "blocking"]
    unresolved_conflicts = [c for c in conflicts if c.status == "unresolved"]

    # Check for blocking policy conflicts
    policy_blocked = any(
        g.gap_type == "system_policy_conflict" for g in blocking_gaps
    )

    if policy_blocked:
        return ContextReadiness(
            status="blocked_by_policy",
            blocking_gap_ids=[g.gap_id for g in blocking_gaps],
            unresolved_conflict_ids=[c.conflict_id for c in unresolved_conflicts],
            next_stage="stop",
        )

    # Check for user decision gaps
    user_gaps = [g for g in blocking_gaps if g.gap_type == "user_decision_required"]
    if user_gaps:
        return ContextReadiness(
            status="needs_clarification",
            blocking_gap_ids=[g.gap_id for g in user_gaps],
            unresolved_conflict_ids=[c.conflict_id for c in unresolved_conflicts],
            next_stage="3.3_context_repair",
        )

    # Check for reader evidence gaps
    reader_gaps = [g for g in blocking_gaps if g.gap_type in (
        "paper_evidence_required",
        "repository_evidence_required",
        "environment_evidence_required",
    )]
    if reader_gaps:
        targets = []
        for g in reader_gaps:
            if g.gap_type == "paper_evidence_required":
                targets.append("paper_intelligence")
            elif g.gap_type == "repository_evidence_required":
                targets.append("repository_intelligence")
            elif g.gap_type == "environment_evidence_required":
                targets.append("environment_profiler")

        return ContextReadiness(
            status="needs_reader_reanalysis",
            blocking_gap_ids=[g.gap_id for g in reader_gaps],
            unresolved_conflict_ids=[c.conflict_id for c in unresolved_conflicts],
            reanalysis_targets=list(set(targets)),
            next_stage="3.2_reanalysis",
        )

    # Check if user decisions are unanswered
    unanswered = [d for d in decisions if d.decision == "not_asked"]
    if unanswered:
        return ContextReadiness(
            status="blocked_by_user",
            blocking_gap_ids=["gap_user_decisions"],
            unresolved_conflict_ids=[],
            next_stage="3.3_context_repair",
        )

    # If there are non-blocking gaps with unresolved conflicts
    if unresolved_conflicts and not blocking_gaps:
        return ContextReadiness(
            status="needs_clarification",
            blocking_gap_ids=[],
            unresolved_conflict_ids=[c.conflict_id for c in unresolved_conflicts],
            next_stage="3.3_context_repair",
        )

    return ContextReadiness(
        status="ready_for_idea_transfer_design",
        blocking_gap_ids=[],
        unresolved_conflict_ids=[],
        next_stage="3.4_idea_transfer_design",
    )


# ---------------------------------------------------------------------------
# U7: Stable Context and 3.4 Handoff
# ---------------------------------------------------------------------------


def finalize_research_context(
    context: ResearchContext,
    readiness: ContextReadiness,
) -> ResearchContext:
    """Create a stable (final) research context from a draft.

    Only called when readiness is ready_for_idea_transfer_design.
    """
    if readiness.status != "ready_for_idea_transfer_design":
        raise ValueError(
            f"Cannot finalize context: readiness is {readiness.status}"
        )

    context.readiness = readiness
    context.context_version += 1
    context.context_sha256 = _compute_context_sha256(context)
    return context


def _compute_context_sha256(context: ResearchContext) -> str:
    """Compute a deterministic SHA256 of the research context."""
    data = json.dumps(context.model_dump(exclude={"context_sha256"}), sort_keys=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()


def build_unified_context_result(
    run_id: str,
    paper_status: str,
    repository_status: str,
    readiness: ContextReadiness,
    draft_path: str,
    report_path: str,
    stable_path: str | None = None,
    handoff_path: str | None = None,
    warnings: list[str] | None = None,
) -> UnifiedResearchContextResult:
    """Build the final UnifiedResearchContextResult."""
    return UnifiedResearchContextResult(
        schema_version=1,
        run_id=run_id,
        paper_capability_status=paper_status,
        repository_capability_status=repository_status,
        context_readiness_status=readiness.status,
        research_context_draft_path=draft_path,
        context_readiness_report_path=report_path,
        stable_research_context_path=stable_path,
        idea_transfer_handoff_path=handoff_path,
        next_stage=readiness.next_stage,
        warnings=warnings or [],
    )
