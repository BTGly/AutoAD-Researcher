"""Tests for stat_plan.py — Step 2 builder + validators."""

import pytest

from autoad_researcher.experiment.stat_plan import (
    build_stat_plan,
    validate_decision_rule_coverage,
    validate_stat_plan,
)
from autoad_researcher.schemas.experiment_planning import (
    AllSeedsImprovedCondition,
    AlwaysCondition,
    IncompletePairsCondition,
    MeanImprovedAboveThresholdCondition,
    ScientificConclusion,
    ScientificDecisionRule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_rules() -> list[ScientificDecisionRule]:
    return [
        ScientificDecisionRule(
            rule_id="rule_incomplete",
            priority=10,
            description="Insufficient pairs",
            condition=IncompletePairsCondition(min_pairs=3),
            conclusion_code=ScientificConclusion.INCOMPLETE,
            narrative_template="Incomplete.",
        ),
        ScientificDecisionRule(
            rule_id="rule_beneficial",
            priority=20,
            description="All seeds improved",
            condition=AllSeedsImprovedCondition(),
            conclusion_code=ScientificConclusion.BENEFICIAL,
            narrative_template="Beneficial.",
        ),
        ScientificDecisionRule(
            rule_id="rule_worse",
            priority=30,
            description="All seeds degraded",
            condition=AllSeedsImprovedCondition(),  # reuse any condition for test
            conclusion_code=ScientificConclusion.WORSE,
            narrative_template="Worse.",
        ),
        ScientificDecisionRule(
            rule_id="rule_equiv",
            priority=40,
            description="Within equivalence margin",
            condition=MeanImprovedAboveThresholdCondition(threshold=0.005),
            conclusion_code=ScientificConclusion.PRACTICALLY_EQUIVALENT,
            narrative_template="Equivalent.",
        ),
        ScientificDecisionRule(
            rule_id="rule_always",
            priority=99,
            description="Catch-all",
            condition=AlwaysCondition(),
            conclusion_code=ScientificConclusion.MIXED,
            narrative_template="Mixed.",
        ),
    ]


# ---------------------------------------------------------------------------
# build_stat_plan
# ---------------------------------------------------------------------------

def test_build_stat_plan_basic():
    plan = build_stat_plan(
        protocol_fingerprint="fp_abc",
        primary_metric="auroc",
        metric_direction="maximize",
        aggregation="mean",
        dispersion="std",
        missing_run_policy="report_incomplete",
        multiple_variant_policy="descriptive_only",
        decision_rules=_default_rules(),
    )
    assert plan.primary_metric == "auroc"
    assert plan.missing_run_policy == "report_incomplete"
    assert len(plan.plan_fingerprint) == 64
    assert len(plan.decision_rules) == 5


def test_build_stat_plan_fingerprint_deterministic():
    kwargs = dict(
        protocol_fingerprint="fp_abc",
        primary_metric="auroc",
        metric_direction="maximize",
        aggregation="mean",
        dispersion="std",
        missing_run_policy="report_incomplete",
        multiple_variant_policy="descriptive_only",
        decision_rules=_default_rules(),
        plan_id="fixed_sp",
    )
    p1 = build_stat_plan(**kwargs)
    p2 = build_stat_plan(**kwargs)
    assert p1.plan_fingerprint == p2.plan_fingerprint


def test_build_stat_plan_empty_rules_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        build_stat_plan(
            protocol_fingerprint="fp",
            primary_metric="auroc",
            metric_direction="maximize",
            aggregation="mean",
            dispersion="std",
            missing_run_policy="report_incomplete",
            multiple_variant_policy="descriptive_only",
            decision_rules=[],
        )


# ---------------------------------------------------------------------------
# validate_decision_rule_coverage
# ---------------------------------------------------------------------------

def test_validate_coverage_dup_priorities():
    rules = [
        ScientificDecisionRule(
            rule_id="r1", priority=10, description="a",
            condition=AlwaysCondition(),
            conclusion_code=ScientificConclusion.MIXED,
            narrative_template="x",
        ),
        ScientificDecisionRule(
            rule_id="r2", priority=10, description="b",
            condition=AlwaysCondition(),
            conclusion_code=ScientificConclusion.MIXED,
            narrative_template="x",
        ),
    ]
    issues = validate_decision_rule_coverage(rules)
    blocking = [i for i in issues if i.severity == "blocking"]
    assert any("Duplicate" in i.message for i in blocking)


def test_validate_coverage_missing_conclusion():
    rules = [
        ScientificDecisionRule(
            rule_id="r1", priority=10, description="only mixed",
            condition=AlwaysCondition(),
            conclusion_code=ScientificConclusion.MIXED,
            narrative_template="x",
        ),
    ]
    issues = validate_decision_rule_coverage(rules)
    blocking = [i for i in issues if i.severity == "blocking"]
    assert len(blocking) >= 4  # BENEFICIAL, EQUIVALENT, WORSE, INCOMPLETE missing


def test_validate_coverage_zero_always():
    rules = _default_rules()
    rules = [r for r in rules if not isinstance(r.condition, AlwaysCondition)]
    issues = validate_decision_rule_coverage(rules)
    always_issue = [i for i in issues if "Exactly one ALWAYS" in i.message]
    assert len(always_issue) == 1


def test_validate_coverage_two_always():
    rules = _default_rules() + [
        ScientificDecisionRule(
            rule_id="r_extra", priority=100, description="extra always",
            condition=AlwaysCondition(),
            conclusion_code=ScientificConclusion.MIXED,
            narrative_template="x",
        ),
    ]
    issues = validate_decision_rule_coverage(rules)
    always_issue = [i for i in issues if "Exactly one ALWAYS" in i.message]
    assert len(always_issue) == 1


def test_validate_coverage_always_not_last():
    rules = [
        ScientificDecisionRule(
            rule_id="r_always", priority=10, description="always first",
            condition=AlwaysCondition(),
            conclusion_code=ScientificConclusion.MIXED,
            narrative_template="x",
        ),
        ScientificDecisionRule(
            rule_id="r_ben", priority=20, description="ben",
            condition=AllSeedsImprovedCondition(),
            conclusion_code=ScientificConclusion.BENEFICIAL,
            narrative_template="x",
        ),
    ]
    issues = validate_decision_rule_coverage(rules)
    always_issue = [i for i in issues if "lowest priority" in i.message]
    assert len(always_issue) == 1


def test_validate_coverage_always_not_mixed():
    rules = _default_rules()
    fallback = next(r for r in rules if isinstance(r.condition, AlwaysCondition))
    fallback.conclusion_code = ScientificConclusion.BENEFICIAL
    issues = validate_decision_rule_coverage(rules)
    mixed_issue = [i for i in issues if "must conclude MIXED" in i.message]
    assert len(mixed_issue) == 1


def test_validate_coverage_incomplete_not_first():
    rules = [
        ScientificDecisionRule(
            rule_id="r_ben", priority=10, description="ben first",
            condition=AllSeedsImprovedCondition(),
            conclusion_code=ScientificConclusion.BENEFICIAL,
            narrative_template="x",
        ),
        ScientificDecisionRule(
            rule_id="r_incomplete", priority=20, description="inc",
            condition=IncompletePairsCondition(min_pairs=3),
            conclusion_code=ScientificConclusion.INCOMPLETE,
            narrative_template="x",
        ),
        ScientificDecisionRule(
            rule_id="r_always", priority=99, description="always",
            condition=AlwaysCondition(),
            conclusion_code=ScientificConclusion.MIXED,
            narrative_template="x",
        ),
    ]
    issues = validate_decision_rule_coverage(rules)
    inc_issue = [i for i in issues if "INCOMPLETE" in i.message]
    assert len(inc_issue) == 1


def test_validate_coverage_valid_rules():
    issues = validate_decision_rule_coverage(_default_rules())
    assert not issues


# ---------------------------------------------------------------------------
# validate_stat_plan
# ---------------------------------------------------------------------------

def test_stat_plan_no_effect_no_issues():
    plan = build_stat_plan(
        protocol_fingerprint="fp",
        primary_metric="auroc",
        metric_direction="maximize",
        aggregation="mean",
        dispersion="std",
        missing_run_policy="report_incomplete",
        multiple_variant_policy="descriptive_only",
        decision_rules=_default_rules(),
    )
    issues = validate_stat_plan(plan)
    assert not issues


class MockEvidenceIndex:
    def __init__(self, ids: set[str]):
        self._ids = ids

    def exists(self, eid: str) -> bool:
        return eid in self._ids


def test_stat_plan_effect_with_evidence():
    plan = build_stat_plan(
        protocol_fingerprint="fp",
        primary_metric="auroc",
        metric_direction="maximize",
        aggregation="mean",
        dispersion="std",
        missing_run_policy="report_incomplete",
        multiple_variant_policy="descriptive_only",
        decision_rules=_default_rules(),
        minimum_meaningful_effect=0.005,
        minimum_meaningful_effect_evidence_ids=["ev_01"],
    )
    idx = MockEvidenceIndex({"ev_01"})
    issues = validate_stat_plan(plan, idx)
    assert not issues


def test_stat_plan_effect_with_user_confirm():
    plan = build_stat_plan(
        protocol_fingerprint="fp",
        primary_metric="auroc",
        metric_direction="maximize",
        aggregation="mean",
        dispersion="std",
        missing_run_policy="report_incomplete",
        multiple_variant_policy="descriptive_only",
        decision_rules=_default_rules(),
        minimum_meaningful_effect=0.005,
        user_confirmation_evidence_id="uconf_01",
    )
    idx = MockEvidenceIndex({"uconf_01"})
    issues = validate_stat_plan(plan, idx)
    assert not issues


def test_stat_plan_effect_without_evidence():
    plan = build_stat_plan(
        protocol_fingerprint="fp",
        primary_metric="auroc",
        metric_direction="maximize",
        aggregation="mean",
        dispersion="std",
        missing_run_policy="report_incomplete",
        multiple_variant_policy="descriptive_only",
        decision_rules=_default_rules(),
        minimum_meaningful_effect=0.005,
    )
    issues = validate_stat_plan(plan)
    blocking = [i for i in issues if i.severity == "blocking"]
    assert len(blocking) >= 1
    assert "minimum_meaningful_effect" in blocking[0].message
