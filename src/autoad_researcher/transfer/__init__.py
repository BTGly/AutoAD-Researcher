"""Transfer design module — Step 3.4 Idea & Transfer Design.

Routes idea sources, confirms one idea, aligns with baseline architecture,
analyzes per-variant compatibility, manages risks, validates invariants,
and hands off to Step 3.5 Experiment Planner.
"""

from autoad_researcher.transfer.aligner import AlignerResult, align_idea_to_baseline
from autoad_researcher.transfer.compatibility import (
    analyze_all_variants,
    analyze_variant,
    filter_variants,
)
from autoad_researcher.transfer.handoff import build_handoff
from autoad_researcher.transfer.normalizer import confirm_idea_contract, normalize_idea_contract
from autoad_researcher.transfer.reanalysis import (
    build_paper_reanalysis,
    build_repository_reanalysis,
    build_spawn_child_run,
)
from autoad_researcher.transfer.risks import accept_risk, build_variant_risk_report
from autoad_researcher.transfer.router import (
    RoutingResult,
    resolve_paper_candidates,
    route_user_idea,
    route_user_original_idea,
)
from autoad_researcher.transfer.selector import (
    is_blocked_no_selection,
    recommend_variants,
    reject_risk_from_selection,
    select_variants,
)
from autoad_researcher.transfer.validator import classify_unresolved, validate_transfer
from autoad_researcher.transfer.variants import generate_variants

__all__ = [
    "AlignerResult",
    "RoutingResult",
    "accept_risk",
    "align_idea_to_baseline",
    "analyze_all_variants",
    "analyze_variant",
    "build_handoff",
    "build_paper_reanalysis",
    "build_repository_reanalysis",
    "build_spawn_child_run",
    "build_variant_risk_report",
    "classify_unresolved",
    "confirm_idea_contract",
    "filter_variants",
    "generate_variants",
    "is_blocked_no_selection",
    "normalize_idea_contract",
    "recommend_variants",
    "reject_risk_from_selection",
    "resolve_paper_candidates",
    "route_user_idea",
    "route_user_original_idea",
    "select_variants",
    "validate_transfer",
]
