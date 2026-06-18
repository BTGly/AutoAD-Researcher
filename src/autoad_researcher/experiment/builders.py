"""Step 5-7: Resolution plans, resource budget, guard policy builders."""

import hashlib

from autoad_researcher.schemas.experiment_planning import (
    ExperimentMatrix,
    ExperimentalResolutionPlan,
    ExperimentalResolutionPlans,
    OperationalGuard,
    OperationalGuardPolicy,
    RangeCriterion,
    ResolutionOutcome,
    ResourceLimits,
)


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
            dim_id = compute_unresolved_dimension_id(
                vi.variant.variant_id, u.dimension.value, u.verification_target or "none"
            )

            target_ids = [
                e.entry_id for e in matrix.entries
                if e.variant_id == vi.variant.variant_id and e.stage in ("full", "smoke")
            ]

            resolutions.append(ExperimentalResolutionPlan(
                unresolved_dimension_id=dim_id,
                dimension=u.dimension.value,
                variant_id=vi.variant.variant_id,
                verification_stage="full",
                target_entry_ids=target_ids,
                observable=u.verification_target or "unknown",
                observation_source="metrics.json",
                acceptance_criterion=RangeCriterion(
                    metric_name=u.verification_target or "metric",
                    lower_bound=0.0,
                    upper_bound=1.0,
                ),
                result_on_accept=ResolutionOutcome.RESOLVED_COMPATIBLE,
                result_on_inconclusive=ResolutionOutcome.INCONCLUSIVE,
            ))

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
    per_variant: dict,
    total_estimate,
    budget_decision,
    budget_id: str = "",
) -> "ResourceBudget":
    from autoad_researcher.schemas.experiment_planning import ResourceBudget

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
    policy_id: str = "",
) -> OperationalGuardPolicy:
    entry_ids = [e.entry_id for e in matrix.entries]

    return OperationalGuardPolicy(
        policy_id=policy_id or _guard_id(),
        schema_version=1,
        protocol_fingerprint=protocol_fingerprint,
        guards=[
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
            OperationalGuard(
                guard_id="g_resource_cap",
                guard_type="global_resource_cap",
                target_entry_ids=["*"],
                parameters={"max_gpu_hours": 100},
                action="stop_all",
                is_blocking=True,
            ),
        ],
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
