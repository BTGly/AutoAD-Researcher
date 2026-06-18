"""Step 5-7: Resolution plans, resource budget, guard policy builders."""

import hashlib

from autoad_researcher.schemas.experiment_planning import (
    BudgetDecision,
    EntryResourceEstimate,
    ExperimentBundleResourceBudget,
    ExperimentMatrix,
    ExperimentalResolutionPlans,
    OperationalGuard,
    OperationalGuardPolicy,
    ResourceBudget,
    ResourceLimits,
    VariantResourceSummary,
)


class ResolutionPlanBuildError(Exception):
    """Raised when unresolved dimensions lack structured acceptance criteria."""


class ResourceBudgetBuildError(Exception):
    """Raised when budget estimates do not exactly cover the matrix."""


# ---------------------------------------------------------------------------
# Step 5 — ExperimentalResolutionPlans
# ---------------------------------------------------------------------------


def build_resolution_plans(
    stage35_input,
    matrix: ExperimentMatrix,
    protocol_fingerprint: str,
    plans_id: str = "",
) -> ExperimentalResolutionPlans:
    resolutions = []
    for vi in stage35_input.variants:
        for u in vi.experiment_resolvable:
            raise ResolutionPlanBuildError(
                "Structured resolution criteria are required before compiling "
                f"experiment_resolvable dimension {vi.variant.variant_id}:"
                f"{u.dimension.value}. Free-text acceptance_criterion is not "
                "converted into a placeholder criterion."
            )

    return ExperimentalResolutionPlans(
        plans_id=plans_id or _rp_id(),
        schema_version=1,
        protocol_fingerprint=protocol_fingerprint,
        resolutions=resolutions,
    )


def compute_unresolved_dimension_id(variant_id: str, dimension: str, observation_source: str) -> str:
    raw = f"{variant_id}::{dimension}::{observation_source}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Step 6 — ResourceBudget
# ---------------------------------------------------------------------------


def build_resource_budget(
    matrix: ExperimentMatrix,
    protocol_fingerprint: str,
    protocol_version: int,
    limits: ResourceLimits,
    entry_estimates: list[EntryResourceEstimate],
    budget_id: str = "",
) -> ResourceBudget:
    entry_by_id = {entry.entry_id: entry for entry in matrix.entries}
    estimate_ids = [estimate.entry_id for estimate in entry_estimates]
    estimate_id_set = set(estimate_ids)
    if len(estimate_ids) != len(estimate_id_set):
        raise ResourceBudgetBuildError("duplicate entry_id in entry_estimates")
    missing = sorted(set(entry_by_id) - estimate_id_set)
    extra = sorted(estimate_id_set - set(entry_by_id))
    if missing:
        raise ResourceBudgetBuildError(
            f"entry_estimates missing matrix entries: {missing}"
        )
    if extra:
        raise ResourceBudgetBuildError(
            f"entry_estimates contains non-matrix entries: {extra}"
        )

    estimates_by_entry = {estimate.entry_id: estimate for estimate in entry_estimates}
    grouped: dict[str, list[EntryResourceEstimate]] = {}
    variant_id_by_group: dict[str, str | None] = {}
    for entry in matrix.entries:
        key = entry.variant_id or "baseline"
        grouped.setdefault(key, []).append(estimates_by_entry[entry.entry_id])
        variant_id_by_group[key] = entry.variant_id

    per_variant = {
        key: VariantResourceSummary(
            variant_id=variant_id_by_group[key],
            entries=entries,
            total_gpu_hours=sum(e.planning_value for e in entries),
            total_wall_clock_hours=sum(e.planning_value for e in entries),
        )
        for key, entries in grouped.items()
    }
    total_gpu_hours = sum(s.total_gpu_hours for s in per_variant.values())
    total_wall_clock_hours = sum(s.total_wall_clock_hours for s in per_variant.values())
    max_single = max((e.planning_value for e in entry_estimates), default=0.0)
    total_estimate = ExperimentBundleResourceBudget(
        total_gpu_hours=total_gpu_hours,
        total_wall_clock_hours=total_wall_clock_hours,
        max_single_experiment_gpu_hours=max_single,
    )
    over_budget_items = []
    if total_gpu_hours > limits.max_total_gpu_hours:
        over_budget_items.append("total_gpu_hours")
    if max_single > limits.max_per_experiment_gpu_hours:
        over_budget_items.append("max_single_experiment_gpu_hours")
    budget_decision = BudgetDecision(
        status="revision_required" if over_budget_items else "within_budget",
        original_limits=limits,
        estimated_consumption=total_estimate,
        utilization_pct=(
            0.0 if limits.max_total_gpu_hours == 0
            else (total_gpu_hours / limits.max_total_gpu_hours) * 100.0
        ),
        over_budget_items=over_budget_items,
    )

    return ResourceBudget(
        budget_id=budget_id or _budget_id(),
        schema_version=1,
        protocol_fingerprint=protocol_fingerprint,
        protocol_version=protocol_version,
        limits=limits,
        per_variant=per_variant,
        total_estimate=total_estimate,
        budget_decision=budget_decision,
    )


# ---------------------------------------------------------------------------
# Step 7 — OperationalGuardPolicy
# ---------------------------------------------------------------------------


def build_guard_policy(
    matrix: ExperimentMatrix,
    protocol_fingerprint: str,
    resource_budget: ResourceBudget | None = None,
    policy_id: str = "",
) -> OperationalGuardPolicy:
    entry_ids = [e.entry_id for e in matrix.entries]
    guards = [
        OperationalGuard(
            guard_id="g_timeout",
            guard_type="timeout",
            target_entry_ids=["*"],
            parameters={"max_seconds": 7200, "grace_seconds": 300},
            action="stop_entry",
            is_blocking=False,
        ),
        OperationalGuard(
            guard_id="g_nan",
            guard_type="nan_inf_detected",
            target_entry_ids=["*"],
            parameters={"max_nan_count": 5},
            action="stop_entry",
            is_blocking=True,
        ),
        OperationalGuard(
            guard_id="g_crash_smoke",
            guard_type="crash_detected",
            target_entry_ids=[eid for eid in entry_ids if "smoke" in eid],
            parameters={},
            action="stop_variant",
            is_blocking=True,
        ),
    ]
    if resource_budget is not None:
        guards.append(
            OperationalGuard(
                guard_id="g_resource_cap",
                guard_type="global_resource_cap",
                target_entry_ids=["*"],
                parameters={
                    "max_gpu_hours": resource_budget.limits.max_total_gpu_hours
                },
                action="stop_all",
                is_blocking=True,
            )
        )

    return OperationalGuardPolicy(
        policy_id=policy_id or _guard_id(),
        schema_version=1,
        protocol_fingerprint=protocol_fingerprint,
        guards=guards,
    )


def _rp_id() -> str:
    import uuid
    return f"rp_{uuid.uuid4().hex[:8]}"


def _budget_id() -> str:
    import uuid
    return f"budget_{uuid.uuid4().hex[:8]}"


def _guard_id() -> str:
    import uuid
    return f"gp_{uuid.uuid4().hex[:8]}"
