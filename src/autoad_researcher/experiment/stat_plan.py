"""Step 2: StatisticalAnalysisPlan builder + rule-coverage validator."""

import hashlib
import json

from autoad_researcher.schemas.experiment_planning import (
    AlwaysCondition,
    ScientificConclusion,
    ScientificDecisionRule,
    StatisticalAnalysisPlan,
)
from autoad_researcher.schemas.experiment_planning import (
    ExperimentPlanValidationIssue as ValidationIssue,
)


def build_stat_plan(
    protocol_fingerprint: str,
    primary_metric: str,
    metric_direction: str,
    aggregation: str,
    dispersion: str,
    missing_run_policy: str,
    multiple_variant_policy: str,
    decision_rules: list[ScientificDecisionRule],
    plan_id: str = "",
    metric_scale: str | None = None,
    minimum_meaningful_effect: float | None = None,
    minimum_meaningful_effect_source: str | None = None,
    minimum_meaningful_effect_evidence_ids: list[str] | None = None,
    user_confirmation_evidence_id: str | None = None,
    paired_by_seed: bool = True,
    max_rerun_attempts: int = 1,
) -> StatisticalAnalysisPlan:
    """Build StatisticalAnalysisPlan with fingerprint.

    Does not validate rule coverage or evidence — call
    ``validate_decision_rule_coverage`` and ``validate_stat_plan`` separately.
    """

    plan = StatisticalAnalysisPlan(
        plan_id=plan_id or _generate_plan_id(),
        schema_version=1,
        protocol_fingerprint=protocol_fingerprint,
        primary_metric=primary_metric,
        metric_direction=metric_direction,
        metric_scale=metric_scale,
        aggregation=aggregation,
        dispersion=dispersion,
        paired_by_seed=paired_by_seed,
        minimum_meaningful_effect=minimum_meaningful_effect,
        minimum_meaningful_effect_source=minimum_meaningful_effect_source,
        minimum_meaningful_effect_evidence_ids=minimum_meaningful_effect_evidence_ids or [],
        user_confirmation_evidence_id=user_confirmation_evidence_id,
        missing_run_policy=missing_run_policy,
        max_rerun_attempts=max_rerun_attempts,
        multiple_variant_policy=multiple_variant_policy,
        decision_rules=decision_rules,
        plan_fingerprint="",
    )

    fp = _sha_model(plan)
    plan.plan_fingerprint = fp
    plan.model_validate(plan.model_dump())
    return plan


def validate_decision_rule_coverage(
    rules: list[ScientificDecisionRule],
) -> list[ValidationIssue]:
    """Verify rules are ordered, complete, with exactly one ALWAYS catch-all.

    First-match priority: rules are evaluated in ascending priority order.
    """

    issues: list[ValidationIssue] = []

    priorities = [r.priority for r in rules]
    if len(priorities) != len(set(priorities)):
        issues.append(ValidationIssue(
            issue_id="stat_dup_priority",
            severity="blocking",
            invariant_category="statistics",
            message="Duplicate rule priorities",
        ))

    covered = {r.conclusion_code for r in rules}
    missing = set(ScientificConclusion.__members__.values()) - covered
    for m in missing:
        issues.append(ValidationIssue(
            issue_id=f"stat_missing_{m.value}",
            severity="blocking",
            invariant_category="statistics",
            message=f"ScientificConclusion.{m.name} not covered by any rule",
        ))

    always_rules = [
        r for r in rules
        if isinstance(r.condition, AlwaysCondition)
    ]
    fallback_rule: ScientificDecisionRule | None = None
    if len(always_rules) != 1:
        issues.append(ValidationIssue(
            issue_id="stat_always_count",
            severity="blocking",
            invariant_category="statistics",
            message=f"Exactly one ALWAYS catch-all required, found {len(always_rules)}",
        ))
    else:
        fallback_rule = always_rules[0]
        if fallback_rule.priority != max(r.priority for r in rules):
            issues.append(ValidationIssue(
                issue_id="stat_always_not_last",
                severity="blocking",
                invariant_category="statistics",
                message="ALWAYS catch-all must have the lowest priority (largest number)",
            ))
        if fallback_rule.conclusion_code != ScientificConclusion.MIXED:
            issues.append(ValidationIssue(
                issue_id="stat_always_not_mixed",
                severity="blocking",
                invariant_category="statistics",
                message="ALWAYS catch-all must conclude MIXED",
            ))

    incomplete_rules = [
        r for r in rules
        if r.conclusion_code == ScientificConclusion.INCOMPLETE
    ]
    other_rules = [
        r for r in rules
        if (fallback_rule is None or r != fallback_rule)
        and r not in incomplete_rules
    ]
    if incomplete_rules and other_rules:
        max_incomplete = max(r.priority for r in incomplete_rules)
        min_other = min(r.priority for r in other_rules)
        if max_incomplete >= min_other:
            issues.append(ValidationIssue(
                issue_id="stat_incomplete_not_first",
                severity="blocking",
                invariant_category="statistics",
                message="All INCOMPLETE rules must have higher priority (smaller number) "
                        "than any BENEFICIAL/WORSE/EQUIVALENT rule",
            ))

    return issues


def validate_stat_plan(
    plan: StatisticalAnalysisPlan,
    evidence_index: object | None = None,
) -> list[ValidationIssue]:
    """Validate evidence backing for minimum_meaningful_effect.

    evidence_index must have an ``exists(evidence_id) -> bool`` method.
    """

    issues: list[ValidationIssue] = []
    if plan.minimum_meaningful_effect is None:
        return issues

    def _exists(eid: str) -> bool:
        if evidence_index is None:
            return False
        return bool(getattr(evidence_index, "exists", lambda _: False)(eid))

    has_evidence = bool(plan.minimum_meaningful_effect_evidence_ids) and all(
        _exists(eid) for eid in plan.minimum_meaningful_effect_evidence_ids
    )
    has_user = (
        plan.user_confirmation_evidence_id is not None
        and _exists(plan.user_confirmation_evidence_id)
    )
    if not has_evidence and not has_user:
        issues.append(ValidationIssue(
            issue_id="stat_no_evidence",
            severity="blocking",
            invariant_category="statistics",
            message="minimum_meaningful_effect set but no valid evidence_ids "
                    "(must exist in EvidenceIndex) or user_confirmation_evidence_id",
        ))

    return issues


def _generate_plan_id() -> str:
    import uuid

    return f"sp_{uuid.uuid4().hex[:8]}"


def _sha_model(model) -> str:
    data = json.dumps(model.model_dump(), sort_keys=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()
