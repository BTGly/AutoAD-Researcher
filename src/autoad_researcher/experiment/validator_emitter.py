"""Step 8: PlanValidator + HandoffEmitter."""

import hashlib
import json
from pathlib import Path

from autoad_researcher.schemas.experiment_planning import (
    PLANNING_ARTIFACT_PATHS,
    ArtifactManifest,
    ExperimentMatrix,
    ExperimentPlanValidationIssue,
    ExperimentPlanValidationReport,
    ExperimentPlannerHandoff,
    ExperimentTrialSpecs,
    ExperimentalResolutionPlans,
    InvariantResult,
    ManifestEntry,
    OperationalGuardPolicy,
    ResourceBudget,
    SharedExperimentProtocol,
    StatisticalAnalysisPlan,
)
from autoad_researcher.experiment.stat_plan import validate_decision_rule_coverage


# ---------------------------------------------------------------------------
# PlanValidator
# ---------------------------------------------------------------------------


class HandoffBlockedError(Exception):
    """Raised when handoff cannot be emitted."""


def validate_plan(
    artifacts: dict[str, Path],
    run_id: str,
    evidence_index: object | None = None,
) -> ExperimentPlanValidationReport:
    """Run all 10 invariant categories against the 7 planning artifacts."""

    issues: list[ExperimentPlanValidationIssue] = []
    invariant_results: list[InvariantResult] = []

    # 1. Structure
    structural_issues = _check_structure(artifacts)
    issues.extend(structural_issues)

    # Load artifacts. Invalid JSON/schema is a blocking structure issue, not a
    # silent None that can accidentally become a passed report.
    protocol = _load_required(
        artifacts, "shared_experiment_protocol.json", SharedExperimentProtocol, issues
    )
    stat_plan = _load_required(
        artifacts, "statistical_analysis_plan.json", StatisticalAnalysisPlan, issues
    )
    specs = _load_required(
        artifacts, "experiment_trial_specs.json", ExperimentTrialSpecs, issues
    )
    matrix = _load_required(
        artifacts, "experiment_matrix.json", ExperimentMatrix, issues
    )
    resolution_plans = _load_required(
        artifacts, "experimental_resolution_plans.json", ExperimentalResolutionPlans, issues
    )
    budget = _load_required(
        artifacts, "resource_budget.json", ResourceBudget, issues
    )
    guard = _load_required(
        artifacts, "operational_guard_policy.json", OperationalGuardPolicy, issues
    )

    all_loaded = all([protocol, stat_plan, specs, matrix, resolution_plans, budget, guard])
    structure_issue_ids = [
        i.issue_id for i in issues if i.invariant_category == "structure"
    ]
    invariant_results.append(InvariantResult(
        category="structure", passed=not structure_issue_ids,
        issue_ids=structure_issue_ids,
    ))

    if not all_loaded:
        # Can't continue without all artifacts loaded
        return _build_report(run_id, issues, invariant_results, artifacts)

    # 2. Baseline fairness
    baseline_issues = _check_baseline_fairness(protocol)
    issues.extend(baseline_issues)
    invariant_results.append(InvariantResult(
        category="baseline_fairness", passed=not baseline_issues,
        issue_ids=[i.issue_id for i in baseline_issues],
    ))

    # 3. Statistics
    stat_issues = _check_statistics(stat_plan, evidence_index)
    issues.extend(stat_issues)
    invariant_results.append(InvariantResult(
        category="statistics", passed=not stat_issues,
        issue_ids=[i.issue_id for i in stat_issues],
    ))

    # 4. Hyperparameter safety
    hp_issues = _check_hyperparameter(specs)
    issues.extend(hp_issues)
    invariant_results.append(InvariantResult(
        category="hyperparameter_safety", passed=not hp_issues,
        issue_ids=[i.issue_id for i in hp_issues],
    ))

    # 5. Cross-variant consistency
    cv_issues = _check_cross_variant(protocol, specs, matrix)
    issues.extend(cv_issues)
    invariant_results.append(InvariantResult(
        category="cross_variant_consistency", passed=not cv_issues,
        issue_ids=[i.issue_id for i in cv_issues],
    ))

    # 6. Resolution coverage
    res_issues = _check_resolution_coverage(resolution_plans, matrix)
    issues.extend(res_issues)
    invariant_results.append(InvariantResult(
        category="resolution_coverage", passed=not res_issues,
        issue_ids=[i.issue_id for i in res_issues],
    ))

    # 7. Budget
    budget_issues = _check_budget(budget, matrix)
    issues.extend(budget_issues)
    invariant_results.append(InvariantResult(
        category="budget", passed=not budget_issues,
        issue_ids=[i.issue_id for i in budget_issues],
    ))

    # 8. Evaluation chain
    eval_issues = _check_eval_chain(protocol)
    issues.extend(eval_issues)
    invariant_results.append(InvariantResult(
        category="evaluation_chain", passed=not eval_issues,
        issue_ids=[i.issue_id for i in eval_issues],
    ))

    # 9. Dependency DAG
    dag_issues = _check_dependency_dag(matrix, specs)
    issues.extend(dag_issues)
    invariant_results.append(InvariantResult(
        category="dependency_dag", passed=not dag_issues,
        issue_ids=[i.issue_id for i in dag_issues],
    ))

    # 10. Uniqueness
    uniq_issues = _check_uniqueness(matrix, specs)
    issues.extend(uniq_issues)
    invariant_results.append(InvariantResult(
        category="uniqueness", passed=not uniq_issues,
        issue_ids=[i.issue_id for i in uniq_issues],
    ))

    return _build_report(run_id, issues, invariant_results, artifacts)


# ---------------------------------------------------------------------------
# HandoffEmitter
# ---------------------------------------------------------------------------

_PLANNING_ARTIFACT_NAMES = list(PLANNING_ARTIFACT_PATHS)
_HANDOFF_MANIFEST_NAMES = [
    *PLANNING_ARTIFACT_PATHS,
    "experiment_plan_validation_report.json",
]


def emit_handoff(
    artifacts: dict[str, Path],
    run_id: str,
    source_sha256: str,
) -> ExperimentPlannerHandoff:
    """Generate 3.5 → 3.6 handoff after all gates pass."""

    # Gate 1: validation report exists and passed
    report = ExperimentPlanValidationReport.model_validate_json(
        artifacts["experiment_plan_validation_report.json"].read_text()
    )
    if report.status != "passed":
        raise HandoffBlockedError("validation report not passed")

    # Gate 1b: TOCTOU — report refs match current artifact hashes
    current_sha = {name: _sha256_file(artifacts[name]) for name in _PLANNING_ARTIFACT_NAMES}
    reported_refs = {r.relative_path: r.sha256 for r in report.validated_artifact_refs}
    for name, sha in current_sha.items():
        if reported_refs.get(name) != sha:
            raise HandoffBlockedError(
                f"validation report binds to different {name} SHA"
            )

    # Gate 1c: cross-artifact fingerprint + report run_id
    protocol = SharedExperimentProtocol.model_validate_json(
        artifacts["shared_experiment_protocol.json"].read_text()
    )
    fp = protocol.protocol_fingerprint

    specs = ExperimentTrialSpecs.model_validate_json(
        artifacts["experiment_trial_specs.json"].read_text()
    )
    stat_plan = StatisticalAnalysisPlan.model_validate_json(
        artifacts["statistical_analysis_plan.json"].read_text()
    )
    matrix = ExperimentMatrix.model_validate_json(
        artifacts["experiment_matrix.json"].read_text()
    )
    resolution_plans = ExperimentalResolutionPlans.model_validate_json(
        artifacts["experimental_resolution_plans.json"].read_text()
    )
    budget = ResourceBudget.model_validate_json(
        artifacts["resource_budget.json"].read_text()
    )
    guard_policy = OperationalGuardPolicy.model_validate_json(
        artifacts["operational_guard_policy.json"].read_text()
    )

    fp_checks = [
        ("specs", specs),
        ("stat_plan", stat_plan),
        ("matrix", matrix),
        ("resolution_plans", resolution_plans),
        ("budget", budget),
        ("guard_policy", guard_policy),
    ]
    for name, obj in fp_checks:
        if getattr(obj, "protocol_fingerprint", None) != fp:
            raise HandoffBlockedError(f"{name}.protocol_fingerprint mismatch")

    if report.protocol_fingerprint != fp:
        raise HandoffBlockedError("report.protocol_fingerprint mismatch")
    if report.run_id != run_id:
        raise HandoffBlockedError(
            f"report.run_id ({report.run_id}) does not match emit run_id ({run_id})"
        )

    # Gate 2: budget accepted
    if budget.budget_decision.status not in ("within_budget", "override_confirmed"):
        raise HandoffBlockedError(
            f"budget status {budget.budget_decision.status} not handoff-ready"
        )

    # Gate 3: manifest exactly artifacts 1-8. Gate 1b intentionally binds only
    # the 7 planning artifacts because the validation report cannot include its
    # own stable SHA.
    manifest_sha = {name: _sha256_file(artifacts[name]) for name in _HANDOFF_MANIFEST_NAMES}
    manifest = ArtifactManifest(entries=[
        ManifestEntry(relative_path=name, sha256=sha, artifact_type=name)
        for name, sha in manifest_sha.items()
    ])

    # Gate 4: selected_variant_ids from specs, matrix consistent
    selected_variant_ids = [v.variant_id for v in specs.variants]
    if matrix.variants != selected_variant_ids:
        raise HandoffBlockedError("matrix.variants mismatch with trial specs")

    validation_report_sha = _sha256_file(
        artifacts["experiment_plan_validation_report.json"]
    )

    return ExperimentPlannerHandoff(
        schema_version=1,
        run_id=run_id,
        source_handoff_sha256=source_sha256,
        artifact_manifest=manifest,
        selected_variant_ids=selected_variant_ids,
        validation_report_sha256=validation_report_sha,
        next_stage="3.6_patch_planner",
    )


# ---------------------------------------------------------------------------
# Invariant check helpers
# ---------------------------------------------------------------------------

_issue = ExperimentPlanValidationIssue  # alias for brevity

def _check_structure(artifacts: dict[str, Path]) -> list[ExperimentPlanValidationIssue]:
    issues = []
    for name in PLANNING_ARTIFACT_PATHS:
        if name not in artifacts or not artifacts[name].exists():
            issues.append(_issue(
                issue_id=f"struct_missing_{name}",
                severity="blocking",
                invariant_category="structure",
                message=f"Missing planning artifact: {name}",
            ))
    return issues


def _check_baseline_fairness(protocol) -> list[ExperimentPlanValidationIssue]:
    issues = []
    if protocol.baseline_policy.mode == "reuse_existing":
        source = protocol.baseline_policy.reuse_source
        if source and source.validity_status != "valid":
            issues.append(_issue(
                issue_id="bl_reuse_invalid",
                severity="blocking",
                invariant_category="baseline_fairness",
                message="Reused baseline must have validity_status=valid",
            ))
        if source is not None:
            identity_checks = [
                (
                    "repository_fingerprint",
                    source.repository_fingerprint,
                    protocol.planning_input_refs.repository_fingerprint,
                ),
                (
                    "baseline_config_sha256",
                    source.baseline_config_sha256,
                    protocol.baseline_config_sha256,
                ),
                (
                    "dataset_manifest_sha256",
                    source.dataset_manifest_sha256,
                    protocol.planning_input_refs.dataset_manifest_sha256,
                ),
                (
                    "environment_lock_sha256",
                    source.environment_lock_sha256,
                    protocol.planning_input_refs.environment_sha256,
                ),
                (
                    "asset_manifest_sha256",
                    source.asset_manifest_sha256,
                    protocol.planning_input_refs.asset_manifest_sha256,
                ),
                (
                    "evaluation_contract_sha256",
                    source.evaluation_contract_sha256,
                    protocol.evaluation_protocol_ref.sha256,
                ),
            ]
            for field_name, actual, expected in identity_checks:
                if actual != expected:
                    issues.append(_issue(
                        issue_id=f"bl_reuse_identity_{field_name}",
                        severity="blocking",
                        invariant_category="baseline_fairness",
                        message=(
                            f"Reused baseline {field_name} must match current "
                            "protocol"
                        ),
                    ))
    return issues


def _check_statistics(stat_plan, evidence_index) -> list[ExperimentPlanValidationIssue]:
    if stat_plan is None:
        return []
    from autoad_researcher.experiment.stat_plan import validate_stat_plan

    issues = list(validate_decision_rule_coverage(stat_plan.decision_rules))
    issues.extend(validate_stat_plan(stat_plan, evidence_index))
    return issues


def _check_hyperparameter(specs) -> list[ExperimentPlanValidationIssue]:
    issues = []
    if specs is None:
        return issues
    for v in specs.variants:
        hp = v.hyperparameter_plan
        if hp.selection_split and hp.selection_split.declared_role == "test":
            issues.append(_issue(
                issue_id=f"hp_test_leak_{v.variant_id}",
                severity="blocking",
                invariant_category="hyperparameter_safety",
                message=f"{v.variant_id}: selection_split cannot use test partition",
            ))
        if hp.mode == "predeclared_search":
            missing = []
            if not hp.selection_split:
                missing.append("selection_split")
            elif hp.selection_split.declared_role != "validation":
                issues.append(_issue(
                    issue_id=f"hp_search_split_not_validation_{v.variant_id}",
                    severity="blocking",
                    invariant_category="hyperparameter_safety",
                    message=(
                        f"{v.variant_id}: predeclared_search selection_split "
                        "must use validation partition"
                    ),
                ))
        if hp.mode == "fixed_from_source" and not hp.source_evidence_ids:
            issues.append(_issue(
                issue_id=f"hp_fixed_source_missing_evidence_{v.variant_id}",
                severity="blocking",
                invariant_category="hyperparameter_safety",
                message=f"{v.variant_id}: fixed_from_source requires source_evidence_ids",
            ))
            if not hp.search_space:
                missing.append("search_space")
            if hp.search_budget is None:
                missing.append("search_budget")
            if hp.selection_metric is None:
                missing.append("selection_metric")
            if missing:
                issues.append(_issue(
                    issue_id=f"hp_search_missing_{v.variant_id}",
                    severity="blocking",
                    invariant_category="hyperparameter_safety",
                    message=(
                        f"{v.variant_id}: predeclared_search missing "
                        f"{', '.join(missing)}"
                    ),
                ))
    return issues


def _check_cross_variant(protocol, specs, matrix) -> list[ExperimentPlanValidationIssue]:
    issues = []
    if specs is None or matrix is None:
        return issues
    if protocol.seeds != matrix.seeds:
        issues.append(_issue(
            issue_id="cv_seed_mismatch", severity="blocking",
            invariant_category="cross_variant_consistency",
            message="protocol.seeds != matrix.seeds",
        ))
    if matrix.variants != [v.variant_id for v in specs.variants]:
        issues.append(_issue(
            issue_id="cv_variant_mismatch", severity="blocking",
            invariant_category="cross_variant_consistency",
            message="matrix.variants mismatch with specs",
        ))
    return issues


def _check_resolution_coverage(plans, matrix) -> list[ExperimentPlanValidationIssue]:
    issues = []
    if plans is None or matrix is None:
        return issues
    all_entry_ids = {e.entry_id for e in matrix.entries}
    entry_by_id = {e.entry_id: e for e in matrix.entries}
    seen_unresolved_ids: set[str] = set()
    for r in plans.resolutions:
        if r.unresolved_dimension_id in seen_unresolved_ids:
            issues.append(_issue(
                issue_id=f"res_duplicate_{r.unresolved_dimension_id[:8]}",
                severity="blocking",
                invariant_category="resolution_coverage",
                message=(
                    f"duplicate unresolved_dimension_id: "
                    f"{r.unresolved_dimension_id}"
                ),
            ))
        seen_unresolved_ids.add(r.unresolved_dimension_id)
        missing = [eid for eid in r.target_entry_ids if eid not in all_entry_ids]
        if missing:
            issues.append(_issue(
                issue_id=f"res_missing_target_{r.unresolved_dimension_id[:8]}",
                severity="blocking",
                invariant_category="resolution_coverage",
                message=f"target_entry_ids {missing} not in matrix",
            ))
        stage_mismatch = [
            eid for eid in r.target_entry_ids
            if eid in entry_by_id and entry_by_id[eid].stage != r.verification_stage
        ]
        if stage_mismatch:
            issues.append(_issue(
                issue_id=f"res_stage_mismatch_{r.unresolved_dimension_id[:8]}",
                severity="blocking",
                invariant_category="resolution_coverage",
                message=(
                    f"target_entry_ids {stage_mismatch} do not match "
                    f"verification_stage {r.verification_stage}"
                ),
            ))
        variant_mismatch = [
            eid for eid in r.target_entry_ids
            if eid in entry_by_id and entry_by_id[eid].variant_id != r.variant_id
        ]
        if variant_mismatch:
            issues.append(_issue(
                issue_id=f"res_variant_mismatch_{r.unresolved_dimension_id[:8]}",
                severity="blocking",
                invariant_category="resolution_coverage",
                message=(
                    f"target_entry_ids {variant_mismatch} do not match "
                    f"variant_id {r.variant_id}"
                ),
            ))
    return issues


def _check_budget(budget, matrix) -> list[ExperimentPlanValidationIssue]:
    issues = []
    if budget is None:
        return issues
    status = budget.budget_decision.status
    if status not in ("within_budget", "override_confirmed"):
        issues.append(_issue(
            issue_id="budget_not_accepted", severity="blocking",
            invariant_category="budget",
            message=f"budget status {status} must be within_budget or override_confirmed",
        ))
    if matrix is not None:
        matrix_entry_ids = {e.entry_id for e in matrix.entries}
        estimates = [
            estimate
            for summary in budget.per_variant.values()
            for estimate in summary.entries
        ]
        estimate_entry_ids = [e.entry_id for e in estimates]
        estimate_id_set = set(estimate_entry_ids)
        if len(estimate_entry_ids) != len(estimate_id_set):
            issues.append(_issue(
                issue_id="budget_duplicate_entry_estimate",
                severity="blocking",
                invariant_category="budget",
                message="resource budget contains duplicate entry estimates",
            ))
        missing = sorted(matrix_entry_ids - estimate_id_set)
        extra = sorted(estimate_id_set - matrix_entry_ids)
        if missing:
            issues.append(_issue(
                issue_id="budget_missing_entry_estimate",
                severity="blocking",
                invariant_category="budget",
                message=f"resource budget missing estimates for entries {missing}",
            ))
        if extra:
            issues.append(_issue(
                issue_id="budget_extra_entry_estimate",
                severity="blocking",
                invariant_category="budget",
                message=f"resource budget has estimates for non-matrix entries {extra}",
            ))
        for key, summary in budget.per_variant.items():
            entry_sum = sum(e.planning_value for e in summary.entries)
            if not _float_eq(summary.total_gpu_hours, entry_sum):
                issues.append(_issue(
                    issue_id=f"budget_variant_total_mismatch_{key}",
                    severity="blocking",
                    invariant_category="budget",
                    message=(
                        f"per_variant {key} total_gpu_hours must equal entry "
                        "planning_value sum"
                    ),
                ))
        summary_total = sum(s.total_gpu_hours for s in budget.per_variant.values())
        if not _float_eq(budget.total_estimate.total_gpu_hours, summary_total):
            issues.append(_issue(
                issue_id="budget_total_mismatch",
                severity="blocking",
                invariant_category="budget",
                message=(
                    "total_estimate.total_gpu_hours must equal per_variant "
                    "total_gpu_hours sum"
                ),
            ))
        max_entry = max((e.planning_value for e in estimates), default=0.0)
        if not _float_eq(budget.total_estimate.max_single_experiment_gpu_hours, max_entry):
            issues.append(_issue(
                issue_id="budget_max_single_mismatch",
                severity="blocking",
                invariant_category="budget",
                message=(
                    "total_estimate.max_single_experiment_gpu_hours must equal "
                    "max entry planning_value"
                ),
            ))
    if status == "within_budget":
        if budget.total_estimate.total_gpu_hours > budget.limits.max_total_gpu_hours:
            issues.append(_issue(
                issue_id="budget_total_limit_exceeded",
                severity="blocking",
                invariant_category="budget",
                message="within_budget total_gpu_hours exceeds max_total_gpu_hours",
            ))
        if (
            budget.total_estimate.max_single_experiment_gpu_hours
            > budget.limits.max_per_experiment_gpu_hours
        ):
            issues.append(_issue(
                issue_id="budget_entry_limit_exceeded",
                severity="blocking",
                invariant_category="budget",
                message=(
                    "within_budget max_single_experiment_gpu_hours exceeds "
                    "max_per_experiment_gpu_hours"
                ),
            ))
    return issues


def _check_dependency_dag(matrix, specs) -> list[ExperimentPlanValidationIssue]:
    issues = []
    if matrix is None:
        return issues
    entry_ids = {e.entry_id for e in matrix.entries}
    entry_by_id = {e.entry_id: e for e in matrix.entries}
    variant_by_id = {
        v.variant_id: v
        for v in specs.variants
    } if specs is not None else {}
    intent_by_id = {}
    if specs is not None:
        if specs.baseline is not None:
            intent_by_id[specs.baseline.intent_id] = specs.baseline
        for v in specs.variants:
            if v.fit is not None:
                intent_by_id[v.fit.intent_id] = v.fit
            intent_by_id[v.smoke.intent_id] = v.smoke
            intent_by_id[v.full.intent_id] = v.full
    for e in matrix.entries:
        for dep in e.depends_on:
            if dep not in entry_ids:
                issues.append(_issue(
                    issue_id=f"dag_missing_{e.entry_id}_{dep}",
                    severity="blocking",
                    invariant_category="dependency_dag",
                    message=f"entry {e.entry_id} depends on non-existent {dep}",
                ))
    # Check for cycles
    if not issues:
        issues.extend(_check_no_cycles(matrix))
    # Check bindings
    for b in matrix.input_bindings:
        consumer = entry_by_id.get(b.consumer_entry_id)
        producer = entry_by_id.get(b.producer_entry_id)
        if consumer is None:
            issues.append(_issue(
                issue_id=f"dag_binding_consumer_{b.consumer_entry_id}",
                severity="blocking",
                invariant_category="dependency_dag",
                message=f"binding consumer {b.consumer_entry_id} not in matrix",
            ))
        if producer is None:
            issues.append(_issue(
                issue_id=f"dag_binding_producer_{b.producer_entry_id}",
                severity="blocking",
                invariant_category="dependency_dag",
                message=f"binding producer {b.producer_entry_id} not in matrix",
            ))
        if consumer is None or producer is None:
            continue
        consumer_intent = intent_by_id.get(consumer.intent_ref)
        requirement = None
        if consumer_intent is None:
            issues.append(_issue(
                issue_id=f"dag_binding_consumer_intent_{consumer.entry_id}",
                severity="blocking",
                invariant_category="dependency_dag",
                message=f"consumer intent {consumer.intent_ref} not found in specs",
            ))
        else:
            requirement = next(
                (
                    r for r in consumer_intent.required_inputs
                    if r.requirement_id == b.consumer_requirement_id
                ),
                None,
            )
            if requirement is None:
                issues.append(_issue(
                    issue_id=f"dag_binding_requirement_{b.consumer_entry_id}",
                    severity="blocking",
                    invariant_category="dependency_dag",
                    message=(
                        f"binding requirement {b.consumer_requirement_id} not "
                        f"declared by consumer {b.consumer_entry_id}"
                    ),
                ))
        production = next(
            (
                p for p in producer.expected_outputs
                if p.production_id == b.producer_production_id
            ),
            None,
        )
        if production is None:
            issues.append(_issue(
                issue_id=f"dag_binding_production_{b.producer_entry_id}",
                severity="blocking",
                invariant_category="dependency_dag",
                message=(
                    f"binding production {b.producer_production_id} not "
                    f"declared by producer {b.producer_entry_id}"
                ),
            ))
        if requirement is not None and production is not None:
            if requirement.artifact_type != production.artifact_type:
                issues.append(_issue(
                    issue_id=f"dag_binding_artifact_type_{b.consumer_entry_id}",
                    severity="blocking",
                    invariant_category="dependency_dag",
                    message=(
                        f"binding artifact type mismatch: requirement "
                        f"{requirement.artifact_type} vs production "
                        f"{production.artifact_type}"
                    ),
                ))
        if consumer.variant_id != producer.variant_id:
            issues.append(_issue(
                issue_id=f"dag_binding_variant_{b.consumer_entry_id}",
                severity="blocking",
                invariant_category="dependency_dag",
                message=(
                    f"binding crosses variants: consumer {consumer.variant_id} "
                    f"producer {producer.variant_id}"
                ),
            ))
        variant = variant_by_id.get(consumer.variant_id) if consumer.variant_id else None
        if (
            variant is not None
            and variant.fit_seed_policy == "per_evaluation_seed"
            and producer.stage == "fit"
            and consumer.seed != producer.seed
        ):
            issues.append(_issue(
                issue_id=f"dag_binding_seed_{b.consumer_entry_id}",
                severity="blocking",
                invariant_category="dependency_dag",
                message=(
                    "per_evaluation_seed binding must use the same producer "
                    "and consumer seed"
                ),
            ))
    return issues


def _check_no_cycles(matrix) -> list[ExperimentPlanValidationIssue]:
    issues = []
    entry_ids = {e.entry_id for e in matrix.entries}
    visited: set[str] = set()
    path: set[str] = set()

    def _dfs(eid: str):
        if eid in path:
            issues.append(_issue(
                issue_id=f"dag_cycle_{eid}", severity="blocking",
                invariant_category="dependency_dag",
                message=f"dependency cycle detected at {eid}",
            ))
            return
        if eid in visited or eid not in entry_ids_map:
            return
        visited.add(eid)
        path.add(eid)
        for dep in entry_ids_map.get(eid, []):
            _dfs(dep)
        path.discard(eid)

    entry_ids_map = {e.entry_id: e.depends_on for e in matrix.entries}
    for eid in entry_ids:
        _dfs(eid)

    return issues


def _check_uniqueness(matrix, specs) -> list[ExperimentPlanValidationIssue]:
    issues = []
    if matrix is None:
        return issues
    eids = [e.entry_id for e in matrix.entries]
    if len(eids) != len(set(eids)):
        issues.append(_issue(
            issue_id="uniq_dup_entry", severity="blocking",
            invariant_category="uniqueness",
            message="duplicate entry_id in matrix",
        ))
    if len(matrix.seeds) != len(set(matrix.seeds)):
        issues.append(_issue(
            issue_id="uniq_dup_seed", severity="blocking",
            invariant_category="uniqueness",
            message="duplicate seeds",
        ))
    return issues


def _check_eval_chain(protocol) -> list[ExperimentPlanValidationIssue]:
    issues = []
    if not protocol.evaluation_protocol_ref:
        issues.append(_issue(
            issue_id="eval_missing_ref", severity="blocking",
            invariant_category="evaluation_chain",
            message="evaluation_protocol_ref is required",
        ))
    supplemental_checks = [
        (
            "evaluator",
            protocol.supplemental_refs.evaluator_ref,
            protocol.supplemental_refs.evaluator_coverage_evidence_ids,
        ),
        (
            "metric_parser",
            protocol.supplemental_refs.metric_parser_ref,
            protocol.supplemental_refs.metric_parser_coverage_evidence_ids,
        ),
        (
            "postprocessing",
            protocol.supplemental_refs.postprocessing_ref,
            protocol.supplemental_refs.postprocessing_coverage_evidence_ids,
        ),
        (
            "dataset_split",
            protocol.supplemental_refs.dataset_split_ref,
            protocol.supplemental_refs.dataset_split_coverage_evidence_ids,
        ),
    ]
    for name, ref, evidence_ids in supplemental_checks:
        if ref is None and not evidence_ids:
            issues.append(_issue(
                issue_id=f"eval_missing_{name}_coverage",
                severity="blocking",
                invariant_category="evaluation_chain",
                message=(
                    f"{name} supplemental ref is absent and has no coverage "
                    "evidence"
                ),
            ))
    return issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_required(artifacts, name, cls, issues):
    if name not in artifacts or not artifacts[name].exists():
        return None
    try:
        return cls.model_validate_json(artifacts[name].read_text())
    except Exception as exc:
        issues.append(_issue(
            issue_id=f"struct_invalid_{_issue_token(name)}",
            severity="blocking",
            invariant_category="structure",
            message=f"Invalid planning artifact {name}: {exc}",
        ))
        return None


def _load_silent(artifacts, name, cls):
    if name not in artifacts or not artifacts[name].exists():
        return None
    try:
        return cls.model_validate_json(artifacts[name].read_text())
    except Exception:
        return None


def _build_report(run_id, issues, invariant_results, artifacts):
    fp = "unknown"
    try:
        proto = _load_silent(
            artifacts, "shared_experiment_protocol.json", SharedExperimentProtocol
        )
        if proto:
            fp = proto.protocol_fingerprint
    except Exception:
        pass

    return ExperimentPlanValidationReport(
        report_id=f"vrep_{run_id}",
        run_id=run_id,
        protocol_fingerprint=fp,
        status="failed" if any(i.severity == "blocking" for i in issues) else "passed",
        invariant_results=invariant_results,
        issues=issues,
        validated_artifact_refs=_build_validated_refs(artifacts),
    )


def _build_validated_refs(artifacts):
    from autoad_researcher.schemas.experiment_planning import ValidatedArtifactRef

    return [
        ValidatedArtifactRef(relative_path=p, sha256=_sha256_file(artifacts[p]))
        for p in PLANNING_ARTIFACT_PATHS
        if p in artifacts and artifacts[p].is_file()
    ]


def _sha256_file(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


_sha256 = _sha256_file


def _issue_token(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_")


def _float_eq(left: float, right: float, tolerance: float = 1e-9) -> bool:
    return abs(left - right) <= tolerance
