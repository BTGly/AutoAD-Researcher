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

    # Load artifacts
    protocol = _load(artifacts, "shared_experiment_protocol.json", SharedExperimentProtocol)
    stat_plan = _load_opt(artifacts, "statistical_analysis_plan.json", StatisticalAnalysisPlan)
    specs = _load_opt(artifacts, "experiment_trial_specs.json", ExperimentTrialSpecs)
    matrix = _load_opt(artifacts, "experiment_matrix.json", ExperimentMatrix)
    resolution_plans = _load_opt(artifacts, "experimental_resolution_plans.json", ExperimentalResolutionPlans)
    budget = _load_opt(artifacts, "resource_budget.json", ResourceBudget)
    guard = _load_opt(artifacts, "operational_guard_policy.json", OperationalGuardPolicy)

    all_loaded = all([protocol, stat_plan, specs, matrix, resolution_plans, budget, guard])

    # 1. Structure
    structural_issues = _check_structure(artifacts)
    issues.extend(structural_issues)
    invariant_results.append(InvariantResult(
        category="structure", passed=not structural_issues,
        issue_ids=[i.issue_id for i in structural_issues],
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
    budget_issues = _check_budget(budget)
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

_PLANNING_ARTIFACT_NAMES = [
    "shared_experiment_protocol.json",
    "statistical_analysis_plan.json",
    "experiment_trial_specs.json",
    "experiment_matrix.json",
    "experimental_resolution_plans.json",
    "resource_budget.json",
    "operational_guard_policy.json",
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

    # Gate 3: manifest exactly artifacts 1-8
    manifest = ArtifactManifest(entries=[
        ManifestEntry(relative_path=name, sha256=sha, artifact_type=name)
        for name, sha in current_sha.items()
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
    for r in plans.resolutions:
        missing = [eid for eid in r.target_entry_ids if eid not in all_entry_ids]
        if missing:
            issues.append(_issue(
                issue_id=f"res_missing_target_{r.unresolved_dimension_id[:8]}",
                severity="blocking",
                invariant_category="resolution_coverage",
                message=f"target_entry_ids {missing} not in matrix",
            ))
    return issues


def _check_budget(budget) -> list[ExperimentPlanValidationIssue]:
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
    return issues


def _check_dependency_dag(matrix, specs) -> list[ExperimentPlanValidationIssue]:
    issues = []
    if matrix is None:
        return issues
    entry_ids = {e.entry_id for e in matrix.entries}
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
        if b.consumer_entry_id not in entry_ids:
            issues.append(_issue(
                issue_id=f"dag_binding_consumer_{b.consumer_entry_id}",
                severity="blocking",
                invariant_category="dependency_dag",
                message=f"binding consumer {b.consumer_entry_id} not in matrix",
            ))
        if b.producer_entry_id not in entry_ids:
            issues.append(_issue(
                issue_id=f"dag_binding_producer_{b.producer_entry_id}",
                severity="blocking",
                invariant_category="dependency_dag",
                message=f"binding producer {b.producer_entry_id} not in matrix",
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
    # Simple structure check: evaluation_protocol_ref must be present
    issues = []
    if not protocol.evaluation_protocol_ref:
        issues.append(_issue(
            issue_id="eval_missing_ref", severity="blocking",
            invariant_category="evaluation_chain",
            message="evaluation_protocol_ref is required",
        ))
    return issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(artifacts, name, cls):
    if name not in artifacts or not artifacts[name].exists():
        return None
    try:
        return cls.model_validate_json(artifacts[name].read_text())
    except Exception:
        return None


_load_opt = _load


def _build_report(run_id, issues, invariant_results, artifacts):
    fp = "unknown"
    try:
        proto = _load(artifacts, "shared_experiment_protocol.json", SharedExperimentProtocol)
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
        if p in artifacts
    ]


def _sha256_file(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


_sha256 = _sha256_file
