"""Unified Research Context (Steps 3.2, 3.3 shared contracts)."""

from autoad_researcher.research_context.assembly import (
    assemble_fact_ledger,
    build_unified_context_result,
    classify_gaps,
    compute_readiness,
    detect_conflicts,
    finalize_research_context,
)
from autoad_researcher.research_context.freeze import (
    active_freeze_context_path,
    freeze_context,
    load_active_freeze_manifest,
)
from autoad_researcher.research_context.models import (
    BaselineContext,
    CandidateDecision,
    ConstraintContext,
    ContextConflict,
    ContextFact,
    ContextReadiness,
    DatasetContext,
    EvidenceBoundary,
    IdeaSourceContext,
    IdeaTransferHandoff,
    InformationGap,
    MetricContext,
    PaperContext,
    RepositoryContext,
    ResearchContext,
    SourceContext,
    SourceEvidenceRef,
    TaskContext,
    UnifiedResearchContextResult,
    UserPreferenceContext,
)
