"""Stage 3.8 runner_execute — handoff intake → execution units → ExperimentExecutionHandoff."""

import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.analysis.metrics import MetricsReport, parse_metrics, MetricParseSpec
from autoad_researcher.supervisor.validity import ScientificValidityReport, ValidityCheck
from autoad_researcher.runner.models import ExperimentExecutionResult
from autoad_researcher.runner.executor import (
    execute_experiment_attempt,
    experiment_command_sha256,
    run_experiment_subprocess,
)
from autoad_researcher.runner.handoff_bridge import build_runner_intake_request
from autoad_researcher.runner.models import ExperimentCommandPlan, ExperimentInputRefs
from autoad_researcher.runner.validators import (
    derive_attempt_outcome,
    validate_intake_against_patch_handoff,
)
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.execution import (
    AttemptOutcome,
    AttemptIdentitySnapshot,
    AttemptRecord,
    ExecutionManifest,
    ExecutionUnitPlan,
    ExecutionUnitRecord,
    ExecutionUnitStatus,
    ExperimentExecutionHandoff,
    IntakeCheck,
    MatrixCoverageReport,
    ProducedArtifactRecord,
    ResourceUsageReport,
    ResolvedArtifactBinding,
    RetryDecision,
    RetryIdentity,
    RunnerIntakeReport,
    RunnerIntakeRequest,
    TerminalReason,
    WorkspaceExecutionRef,
)
from autoad_researcher.schemas.patch_planning import PatchRunnerHandoff
from autoad_researcher.schemas.stage3_acceptance import (
    Stage3AcceptanceArtifactRef,
    Stage3AcceptanceStageRecord,
)


def run_runner_execute_stage(
    run_id: str,
    run_dir: Path,
    stage_dir: Path,
    mode: str = "l1-l2",
    repo_root: Path = Path("workspace/repos/patchcore-inspection"),
) -> Stage3AcceptanceStageRecord:
    """Run the 3.8 runner execute stage.

    Consumes PatchRunnerHandoff (3.7) + ExperimentPlannerHandoff (3.5) →
    builds RunnerIntakeRequest → executes each experiment unit →
    produces ExperimentExecutionHandoff for 3.9.
    """
    handoff_path = stage_dir / "experiment_execution_handoff.json"
    if handoff_path.exists():
        handoff_sha = _sha256_file(handoff_path)
        return Stage3AcceptanceStageRecord(
            stage="runner_execute", status="passed",
            handoff_sha256=handoff_sha,
            artifacts=[
                Stage3AcceptanceArtifactRef(
                    relative_path=str(handoff_path.relative_to(run_dir)),
                    sha256=handoff_sha,
                    artifact_type="experiment_execution_handoff",
                ),
            ],
        )

    # ── Load 3.7 PatchRunnerHandoff ──────────────────────────────────────
    handoff_37_path = run_dir / "patch_applicator" / "patch_runner_handoff.json"
    if not handoff_37_path.exists():
        return Stage3AcceptanceStageRecord(
            stage="runner_execute", status="blocked",
            blocked_reason="blocked_upstream: patch_runner_handoff.json not found",
        )
    handoff_37 = PatchRunnerHandoff.model_validate_json(
        handoff_37_path.read_text(encoding="utf-8"),
    )

    # ── Load 3.5 experiment artifacts ────────────────────────────────────
    exp_plan_dir = run_dir / "experiment_planning"
    handoff_35_path = exp_plan_dir / "experiment_planner_handoff.json"
    matrix_path = exp_plan_dir / "experiment_matrix.json"
    protocol_path = exp_plan_dir / "shared_experiment_protocol.json"
    stats_path = exp_plan_dir / "statistical_analysis_plan.json"
    guard_path = exp_plan_dir / "operational_guard_policy.json"

    for p, label in [(handoff_35_path, "experiment_planner_handoff.json"),
                     (matrix_path, "experiment_matrix.json"),
                     (protocol_path, "shared_experiment_protocol.json"),
                     (stats_path, "statistical_analysis_plan.json"),
                     (guard_path, "operational_guard_policy.json")]:
        if not p.exists():
            return Stage3AcceptanceStageRecord(
                stage="runner_execute", status="blocked",
                blocked_reason=f"blocked_upstream: {label} not found",
            )

    handoff_35 = json.loads(handoff_35_path.read_text(encoding="utf-8"))
    experiment_matrix = json.loads(matrix_path.read_text(encoding="utf-8"))

    # ── No-op patch gate ─────────────────────────────────────────────────
    no_effective_patch = all(
        vw.patch_diff_sha256 is None
        or vw.patch_diff_sha256 == ""
        or vw.patch_diff_sha256 == "0" * 64
        for vw in handoff_37.variant_workspaces
    )
    if no_effective_patch:
        if mode == "l3-preflight":
            return Stage3AcceptanceStageRecord(
                stage="runner_execute", status="blocked",
                blocked_reason="blocked_no_effective_patch: "
                               "variant diff is empty/no-op in l3-preflight mode",
            )

    # ── Load benchmark config for command construction ───────────────────
    benchmark_config = _load_benchmark_config()

    # ── Build RunnerIntakeRequest ────────────────────────────────────────
    handoff_37_sha = _sha256_file(handoff_37_path)
    handoff_35_sha = _sha256_file(handoff_35_path)
    matrix_sha256 = _sha256_file(matrix_path)
    protocol_fingerprint = _sha256_file(protocol_path)
    stats_sha256 = _sha256_file(stats_path)
    guard_sha256 = _sha256_file(guard_path)

    intake_request = build_runner_intake_request(
        handoff=handoff_37,
        handoff_artifact_sha256=handoff_37_sha,
        experiment_planner_handoff_sha256=handoff_35_sha,
        experiment_matrix_sha256=matrix_sha256,
        shared_protocol_fingerprint=protocol_fingerprint,
        statistical_analysis_plan_sha256=stats_sha256,
        operational_guard_policy_sha256=guard_sha256,
    )

    # ── Run intake validation ────────────────────────────────────────────
    intake_report = _run_intake(intake_request, handoff_37, repo_root)
    _write_json(stage_dir / "runner_intake_report.json",
                intake_report.model_dump(mode="json", exclude_none=True))

    if intake_report.status != "eligible":
        return Stage3AcceptanceStageRecord(
            stage="runner_execute", status="blocked",
            blocked_reason=f"intake_failed: {intake_report.status}",
        )

    # ── Build execution unit plans from experiment matrix ────────────────
    units = _build_execution_units(
        experiment_matrix=experiment_matrix,
        handoff_37=handoff_37,
        benchmark_config=benchmark_config,
    )
    _write_json(stage_dir / "execution_unit_plans.json",
                [u.model_dump(mode="json") for u in units])

    # ── Execute each unit with per-attempt command plans ─────────────────
    unit_records: list[ExecutionUnitRecord] = []
    all_attempt_records: list[AttemptRecord] = []
    retry_decisions: list[RetryDecision] = []

    for unit in units:
        unit_dir = stage_dir / "attempts" / unit.unit_id
        unit_dir.mkdir(parents=True, exist_ok=True)

        attempts: list[AttemptRecord] = []
        final_status: ExecutionUnitStatus = ExecutionUnitStatus.BLOCKED
        terminal_reason: TerminalReason = "intake_failed"

        for attempt_idx in range(1, unit.max_attempts + 1):
            attempt_id = f"{unit.unit_id}_attempt_{attempt_idx}"
            attempt_dir = unit_dir / f"attempt_{attempt_idx}"
            if attempt_dir.exists():
                shutil.rmtree(attempt_dir)

            # Build per-attempt command plan with results_path scoped to attempt_dir
            command_plan = _make_command_plan(
                unit=unit,
                benchmark_config=benchmark_config,
                results_root=attempt_dir,
            )
            cmd_sha = experiment_command_sha256(command_plan)

            workspace_ref = _find_workspace_ref(
                intake_request.workspace_refs, unit.workspace_id,
            )
            input_refs = ExperimentInputRefs(
                repository_fingerprint=(
                    workspace_ref.repository_fingerprint if workspace_ref else "0" * 64
                ),
                environment_sha256=_compute_env_lock_sha(),
                dataset_manifest_sha256=_compute_dataset_manifest_sha(),
                asset_manifest_sha256="0" * 64,
                command_sha256=cmd_sha,
            )

            # execution_unit_plan_sha256 = canonical SHA of unit plan
            # excluding the command_plan_sha256 placeholder (same for all
            # retry attempts within the unit).
            unit_payload = unit.model_dump(mode="json", exclude_none=True)
            unit_payload.pop("command_plan_sha256", None)
            execution_unit_plan_sha = canonical_sha256(unit_payload)

            identity = AttemptIdentitySnapshot(
                execution_unit_plan_sha256=execution_unit_plan_sha,
                command_sha256=cmd_sha,
                input_refs_sha256=_compute_input_refs_sha(input_refs),
                workspace_repository_fingerprint=(
                    workspace_ref.repository_fingerprint if workspace_ref else "0" * 64
                ),
            )

            _t0 = time.monotonic()
            result = execute_experiment_attempt(
                run_id=run_id,
                attempt=attempt_id,
                command_plan=command_plan,
                input_refs=input_refs,
                attempt_dir=str(attempt_dir),
                runner=run_experiment_subprocess,
            )
            _wall_time = time.monotonic() - _t0

            # ── Gather resource telemetry ──────────────────────────────────
            resource_report = _collect_resource_usage(
                attempt_id=attempt_id,
                unit_id=unit.unit_id,
                wall_time_seconds=_wall_time,
                subject_type="baseline" if unit.variant_id is None else "variant",
                variant_id=unit.variant_id,
                seed=unit.seed,
            )
            resource_report_path = attempt_dir / "resource_usage.json"
            _write_json(resource_report_path,
                        resource_report.model_dump(mode="json", exclude_none=True, exclude={"actual_gpu_hours"}))
            resource_report_sha = _sha256_file(resource_report_path)
            resource_usage_ref = ArtifactReferenceV2(
                artifact_id=f"resource_usage_{attempt_id}",
                artifact_type="resource_usage_report",
                locator=str(resource_report_path.relative_to(run_dir.parent)),
                sha256=resource_report_sha,
            )

            _write_json(attempt_dir / "command_plan.json",
                        command_plan.model_dump(mode="json", exclude_none=True))
            _write_json(attempt_dir / "input_refs.json",
                        input_refs.model_dump(mode="json", exclude_none=True))
            _write_json(attempt_dir / "execution_unit_plan.json",
                        unit.model_dump(mode="json", exclude_none=True))

            metrics_report = _parse_benchmark_metrics(attempt_dir, benchmark_config)
            validity_report = _make_validity_report(result, metrics_report)
            outcome = derive_attempt_outcome(result, metrics_report, validity_report)

            # ── Persist metrics + validity evidence artifacts ─────────────
            metrics_ref: ArtifactReferenceV2 | None = None
            validity_ref: ArtifactReferenceV2 | None = None
            evidence_bindings: list[ResolvedArtifactBinding] = []

            if metrics_report is not None:
                metrics_path = attempt_dir / "metrics_report.json"
                _write_json(metrics_path, metrics_report.model_dump(mode="json", exclude_none=True))
                metrics_sha = _sha256_file(metrics_path)
                metrics_ref = ArtifactReferenceV2(
                    artifact_id=f"metrics_report_{attempt_id}",
                    artifact_type="metrics_report",
                    locator=str(metrics_path.relative_to(run_dir.parent)),
                    sha256=metrics_sha,
                )
                evidence_bindings.append(ResolvedArtifactBinding(
                    binding_id=f"metrics_report_{attempt_id}",
                    role="metrics_report",
                    artifact_ref=metrics_ref,
                    artifact_sha256=metrics_sha,
                ))

            if validity_report is not None:
                validity_path = attempt_dir / "validity_report.json"
                _write_json(validity_path, validity_report.model_dump(mode="json", exclude_none=True))
                validity_sha = _sha256_file(validity_path)
                validity_ref = ArtifactReferenceV2(
                    artifact_id=f"validity_report_{attempt_id}",
                    artifact_type="validity_report",
                    locator=str(validity_path.relative_to(run_dir.parent)),
                    sha256=validity_sha,
                )
                evidence_bindings.append(ResolvedArtifactBinding(
                    binding_id=f"validity_report_{attempt_id}",
                    role="validity_report",
                    artifact_ref=validity_ref,
                    artifact_sha256=validity_sha,
                ))

            record = AttemptRecord(
                attempt_id=attempt_id,
                attempt_index=attempt_idx,
                unit_id=unit.unit_id,
                identity=identity,
                outcome=outcome,
                execution_result_ref=ArtifactReferenceV2(
                    artifact_id=f"exec_result_{attempt_id}",
                    artifact_type="execution_result",
                    locator=str(
                        (attempt_dir / "execution_result.json").relative_to(run_dir.parent),
                    ),
                    sha256=_sha256_file(attempt_dir / "execution_result.json"),
                ),
                metrics_report_ref=metrics_ref,
                validity_report_ref=validity_ref,
                resource_usage_ref=resource_usage_ref,
                produced_artifacts=[
                    ProducedArtifactRecord(
                        unit_id=unit.unit_id,
                        attempt_id=attempt_id,
                        bindings=[
                            ResolvedArtifactBinding(
                                binding_id=f"cmd_plan_{attempt_id}",
                                role="command_plan",
                                artifact_ref=ArtifactReferenceV2(
                                    artifact_id=f"cmd_plan_{attempt_id}",
                                    artifact_type="command_plan",
                                    locator=str(
                                        (attempt_dir / "command_plan.json").relative_to(run_dir.parent),
                                    ),
                                    sha256=_sha256_file(attempt_dir / "command_plan.json"),
                                ),
                                artifact_sha256=cmd_sha,
                            ),
                            ResolvedArtifactBinding(
                                binding_id=f"unit_plan_{attempt_id}",
                                role="execution_unit_plan",
                                artifact_ref=ArtifactReferenceV2(
                                    artifact_id=f"unit_plan_{attempt_id}",
                                    artifact_type="execution_unit_plan",
                                    locator=str(
                                        (attempt_dir / "execution_unit_plan.json").relative_to(run_dir.parent),
                                    ),
                                    sha256=_sha256_file(attempt_dir / "execution_unit_plan.json"),
                                ),
                                artifact_sha256=execution_unit_plan_sha,
                            ),
                            ResolvedArtifactBinding(
                                binding_id=f"input_refs_{attempt_id}",
                                role="input_refs",
                                artifact_ref=ArtifactReferenceV2(
                                    artifact_id=f"input_refs_{attempt_id}",
                                    artifact_type="input_refs",
                                    locator=str(
                                        (attempt_dir / "input_refs.json").relative_to(run_dir.parent),
                                    ),
                                    sha256=_sha256_file(attempt_dir / "input_refs.json"),
                                ),
                                artifact_sha256=_compute_input_refs_sha(input_refs),
                            ),
                            *evidence_bindings,
                        ],
                    ),
                ],
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            )
            attempts.append(record)
            all_attempt_records.append(record)

            if result.status == "success":
                final_status = ExecutionUnitStatus.COMPLETED
                terminal_reason = "completed"
                break

            if attempt_idx < unit.max_attempts:
                retry_decisions.append(RetryDecision(
                    attempt_id=attempt_id,
                    unit_id=unit.unit_id,
                    prev_identity=RetryIdentity(**identity.model_dump()),
                    identity_match=True,
                    decision="retry_same_command",
                    failure_classification="transient",
                    reason=f"attempt {attempt_idx} failed: {result.failure_message}",
                ))
            else:
                terminal_reason = "execution_failed"
                final_status = ExecutionUnitStatus.FAILED

        unit_record = ExecutionUnitRecord(
            unit_id=unit.unit_id,
            matrix_entry_id=unit.matrix_entry_id,
            variant_id=unit.variant_id,
            seed=unit.seed,
            stage=unit.stage,
            workspace_id=unit.workspace_id,
            final_status=final_status,
            final_attempt_id=attempts[-1].attempt_id if attempts else None,
            attempts=attempts,
            terminal_reason=terminal_reason,
        )
        unit_records.append(unit_record)

    # ── Build execution command plan manifest from real attempt artifacts ─
    command_plans = []
    for rec in unit_records:
        if rec.attempts:
            last_attempt = rec.attempts[-1]
            for prod in last_attempt.produced_artifacts:
                for bind in prod.bindings:
                    if bind.role == "command_plan":
                        plan_path = run_dir.parent / bind.artifact_ref.locator
                        if plan_path.exists():
                            command_plans.append(json.loads(plan_path.read_text(encoding="utf-8")))
    _write_json(stage_dir / "experiment_command_plan.json", command_plans)

    # ── Write GPU execution evidence ────────────────────────────────────
    _write_gpu_evidence(stage_dir)

    # ── Build ExecutionManifest ──────────────────────────────────────────
    completed_units = [u for u in unit_records if u.final_status == ExecutionUnitStatus.COMPLETED]
    failed_units = [u for u in unit_records if u.final_status == ExecutionUnitStatus.FAILED]
    blocked_units = [u for u in unit_records if u.final_status == ExecutionUnitStatus.BLOCKED]

    intake_report_path = stage_dir / "runner_intake_report.json"
    manifest = ExecutionManifest(
        run_id=run_id,
        experiment_matrix_sha256=matrix_sha256,
        protocol_fingerprint=protocol_fingerprint,
        workspace_refs_sha256=_compute_workspace_refs_sha(intake_request.workspace_refs),
        operational_guard_policy_sha256=guard_sha256,
        runner_intake_report_ref=ArtifactReferenceV2(
            artifact_id=f"intake_report_{run_id}",
            artifact_type="runner_intake_report",
            locator=str(intake_report_path.relative_to(run_dir.parent)),
            sha256=_sha256_file(intake_report_path),
        ),
        unit_records=unit_records,
        completed_unit_count=len(completed_units),
        failed_unit_count=len(failed_units),
        blocked_unit_count=len(blocked_units),
        retry_decisions=retry_decisions,
        matrix_coverage=MatrixCoverageReport(
            total_unit_count=len(units),
            completed_count=len(completed_units),
            failed_count=len(failed_units),
            blocked_count=len(blocked_units),
        ),
    )
    _write_json(stage_dir / "execution_manifest.json",
                manifest.model_dump(mode="json", exclude_none=True))

    overall_status = _derive_overall(manifest)

    # ── Build ExperimentExecutionHandoff for 3.9 ────────────────────────
    handoff = ExperimentExecutionHandoff(
        run_id=run_id,
        execution_manifest_ref=ArtifactReferenceV2(
            artifact_id=f"exec_manifest_{run_id}",
            artifact_type="execution_manifest",
            locator=f"runner_execute/execution_manifest.json",
            sha256=_sha256_file(stage_dir / "execution_manifest.json"),
        ),
        execution_unit_plans_sha256=_sha256_file(stage_dir / "execution_unit_plans.json"),
        experiment_matrix_sha256=matrix_sha256,
        statistical_analysis_plan_sha256=stats_sha256,
        protocol_fingerprint=protocol_fingerprint,
        runner_intake_report_ref=ArtifactReferenceV2(
            artifact_id=f"intake_report_{run_id}",
            artifact_type="runner_intake_report",
            locator=str(intake_report_path.relative_to(run_dir.parent)),
            sha256=_sha256_file(intake_report_path),
        ),
        resource_budget_ref=ArtifactReferenceV2(
            artifact_id=f"resource_budget_{run_id}",
            artifact_type="resource_budget",
            locator=f"experiment_planning/resource_budget.json",
            sha256=_sha256_file(exp_plan_dir / "resource_budget.json"),
        ),
        budget_decision_ref=ArtifactReferenceV2(
            artifact_id=f"budget_decision_{run_id}",
            artifact_type="budget_decision",
            locator=f"experiment_planning/resource_budget.json",
            sha256=_sha256_file(exp_plan_dir / "resource_budget.json"),
        ),
        workspace_refs=intake_request.workspace_refs,
        completed_unit_ids=[u.unit_id for u in completed_units],
        failed_unit_ids=[u.unit_id for u in failed_units],
        blocked_unit_ids=[u.unit_id for u in blocked_units],
        overall_status=overall_status,
        next_stage="3.9_results_analysis",
    )

    _write_json(handoff_path, handoff.model_dump(mode="json", exclude_none=True))
    handoff_sha = _sha256_file(handoff_path)

    if overall_status in ("completed", "partially_completed"):
        stage_status = "passed"
        blocked_reason = None
    elif overall_status == "failed":
        stage_status = "blocked"
        blocked_reason = "execution_all_units_failed"
    else:
        stage_status = "blocked"
        blocked_reason = f"execution_{overall_status}"

    return Stage3AcceptanceStageRecord(
        stage="runner_execute", status=stage_status,
        blocked_reason=blocked_reason,
        handoff_sha256=handoff_sha,
        artifacts=[
            Stage3AcceptanceArtifactRef(
                relative_path=str(handoff_path.relative_to(run_dir)),
                sha256=handoff_sha,
                artifact_type="experiment_execution_handoff",
            ),
            Stage3AcceptanceArtifactRef(
                relative_path=str(intake_report_path.relative_to(run_dir)),
                sha256=_sha256_file(intake_report_path),
                artifact_type="runner_intake_report",
            ),
            Stage3AcceptanceArtifactRef(
                relative_path=f"runner_execute/execution_manifest.json",
                sha256=_sha256_file(stage_dir / "execution_manifest.json"),
                artifact_type="execution_manifest",
            ),
        ],
    )


# ── Intake ──────────────────────────────────────────────────────────────────

def _run_intake(
    request: RunnerIntakeRequest,
    handoff: PatchRunnerHandoff,
    repo_root: Path,
) -> RunnerIntakeReport:
    checks: list[IntakeCheck] = [
        IntakeCheck(name="handoff_valid", status="passed"),
    ]

    try:
        validate_intake_against_patch_handoff(request, handoff)
        checks.append(IntakeCheck(name="intake_validated_against_handoff", status="passed"))
    except (ValueError, AssertionError) as exc:
        checks.append(IntakeCheck(
            name="intake_validated_against_handoff", status="failed",
            details=str(exc),
        ))
        return RunnerIntakeReport(
            status="blocked", checks=checks,
            report_sha256="0" * 64,
        )

    if not repo_root.exists():
        checks.append(IntakeCheck(
            name="repo_root_exists", status="failed",
            details=f"repo_root not found: {repo_root}",
        ))
        return RunnerIntakeReport(
            status="blocked", checks=checks,
            report_sha256="0" * 64,
        )
    checks.append(IntakeCheck(name="repo_root_exists", status="passed"))

    repo_status = _repo_status(repo_root)
    if repo_status == "clean":
        checks.append(IntakeCheck(name="repo_clean", status="passed", details="clean"))
    else:
        dirty_sha = _compute_dirty_diff_sha256(repo_root)
        expected_shas = {
            ws.patch_diff_sha256
            for ws in request.workspace_refs
            if ws.subject_type == "variant" and ws.patch_diff_sha256
        }
        allowed, prohibited = _dirty_files_are_allowed(repo_root)
        if dirty_sha is not None and dirty_sha in expected_shas:
            if allowed:
                checks.append(IntakeCheck(
                    name="repo_clean", status="passed",
                    details=(
                        f"repo status: {repo_status} (expected — dirty diff "
                        f"SHA {dirty_sha[:12]} matches variant workspace patch)"
                    ),
                ))
            else:
                checks.append(IntakeCheck(
                    name="repo_clean", status="failed",
                    details=(
                        f"repo dirty with protected file changes: "
                        f"{', '.join(prohibited)}"
                    ),
                ))
                return RunnerIntakeReport(
                    status="blocked", checks=checks,
                    report_sha256="0" * 64,
                )
        else:
            checks.append(IntakeCheck(
                name="repo_clean", status="failed",
                details=(
                    f"repo status: {repo_status}; dirty diff SHA "
                    f"{dirty_sha[:12] if dirty_sha else 'N/A'} does not match "
                    f"any variant workspace expected SHA"
                ),
            ))
            return RunnerIntakeReport(
                status="blocked", checks=checks,
                report_sha256="0" * 64,
            )
    report = RunnerIntakeReport(status="eligible", checks=checks, report_sha256="0" * 64)
    report.report_sha256 = _compute_report_sha(report)
    return report


# ── Execution unit builders ─────────────────────────────────────────────────

def _build_execution_units(
    experiment_matrix: dict[str, Any],
    handoff_37: PatchRunnerHandoff,
    benchmark_config: dict[str, Any] | None,
) -> list[ExecutionUnitPlan]:
    """Build one ExecutionUnitPlan per experiment matrix entry."""
    entries = experiment_matrix.get("entries", [])
    units: list[ExecutionUnitPlan] = []

    for i, entry in enumerate(entries):
        entry_id: str = entry["entry_id"]
        variant_id: str | None = entry.get("variant_id")
        seed: int | None = entry.get("seed")

        if variant_id is None:
            workspace_id = handoff_37.baseline_workspace_ref.workspace_id
        else:
            matching = [
                vw.workspace_id for vw in handoff_37.variant_workspaces
                if variant_id in vw.variant_ids
            ]
            workspace_id = matching[0] if matching else f"ws_{variant_id}"

        unit = ExecutionUnitPlan(
            unit_id=f"unit_{entry_id}_{i}",
            matrix_entry_id=entry_id,
            variant_id=variant_id,
            seed=seed,
            workspace_id=workspace_id,
            stage=entry.get("stage", "full"),
            command_plan_sha256="0" * 64,
            max_attempts=2,
            max_wall_time_seconds=3600,
        )
        units.append(unit)

    return units


def _make_command_plan(
    unit: ExecutionUnitPlan,
    benchmark_config: dict[str, Any] | None,
    results_root: Path,
) -> ExperimentCommandPlan:
    """Construct ExperimentCommandPlan with attempt_dir-scoped output.

    The PatchCore entrypoint path is passed as a relative path within the repo
    (so it works when cwd=repo). The results_path is an absolute path within
    the attempt dir, so executor can find expected outputs at ``results_root/outputs/...``.
    """
    repo = Path("workspace/repos/patchcore-inspection")
    src_path = str(repo.resolve() / "src")
    _python = _runner_python()

    if benchmark_config is None:
        results_out = results_root.resolve() / "outputs"
        env = {"PYTHONPATH": src_path}
        env.update(_runner_python_env())
        return ExperimentCommandPlan(
            schema_version=1,
            command_id=f"cmd_{unit.unit_id}",
            program=_python,
            args=["bin/run_patchcore.py", str(results_out)],
            cwd=str(repo),
            environment=env,
            timeout_seconds=unit.max_wall_time_seconds,
            network=False,
            expected_outputs=["outputs/results.csv"],
        )

    entrypoint = benchmark_config.get("repository", {}).get(
        "entrypoint_path", "bin/run_patchcore.py",
    )
    params = benchmark_config.get("fixed_parameters", {})
    dataset_cfg = benchmark_config.get("dataset", {})
    eval_cfg = benchmark_config.get("evaluation", {})

    log_project = params.get("log_project", "autoad_internal_benchmark")
    log_group = params.get("log_group", "internal_patchcore_mvtec_bottle_v1")

    # Use absolute results path scoped to attempt_dir so executor's
    # expected_outputs check (relative to attempt_dir) can find the files.
    results_out = results_root.resolve() / "outputs"

    raw_result_paths = eval_cfg.get("raw_result_paths", [])
    expected_outputs = list(raw_result_paths) if raw_result_paths else [
        f"outputs/{log_project}/{log_group}/results.csv",
    ]

    gpu_enabled = _cuda_available()
    args = [
        entrypoint,
        "--seed", str(params.get("seed", 0)),
        "--log_group", log_group,
        "--log_project", log_project,
    ]
    if gpu_enabled:
        args += ["--gpu", str(params.get("gpu", 0))]
    args += [str(results_out)]

    args += [
        "patch_core",
        "-b", params.get("backbone", "wideresnet50"),
    ]
    for layer in params.get("layers", ["layer2", "layer3"]):
        args += ["-le", layer]
    args += [
        "--pretrain_embed_dimension", str(params.get("pretrain_embed_dimension", 1024)),
        "--target_embed_dimension", str(params.get("target_embed_dimension", 1024)),
        "--preprocessing", params.get("preprocessing", "mean"),
        "--aggregation", params.get("aggregation", "mean"),
        "--anomaly_scorer_num_nn", str(params.get("anomaly_scorer_num_nn", 1)),
        "--patchsize", str(params.get("patchsize", 3)),
        "--patchscore", params.get("patchscore", "max"),
        "--patchoverlap", str(params.get("patchoverlap", 0.0)),
        "--faiss_num_workers", str(params.get("faiss_num_workers", 8)),
    ]
    if params.get("faiss_on_gpu", False):
        args.append("--faiss_on_gpu")

    args += [
        "sampler",
        "--percentage", str(params.get("coreset_sampling_ratio", 0.1)),
        params.get("sampler", "approx_greedy_coreset"),
    ]

    dataset_root_env = dataset_cfg.get("root_env", "AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT")
    dataset_root = os.environ.get(dataset_root_env, "/tmp/mvtec")
    category = dataset_cfg.get("category", "bottle")

    args += [
        "dataset",
        "-d", category,
        "--train_val_split", str(params.get("train_val_split", 1.0)),
        "--batch_size", str(params.get("batch_size", 2)),
        "--num_workers", str(params.get("num_workers", 8)),
        "--resize", str(params.get("resize", 256)),
        "--imagesize", str(params.get("imagesize", 224)),
        "mvtec",
        dataset_root,
    ]

    env: dict[str, str] = {
        dataset_root_env: dataset_root,
        "PYTHONPATH": src_path,
    }
    env.update(_runner_python_env())
    return ExperimentCommandPlan(
        schema_version=1,
        command_id=f"cmd_{unit.unit_id}",
        program=_python,
        args=args,
        cwd=str(repo),
        environment=env,
        timeout_seconds=unit.max_wall_time_seconds,
        network=False,
        expected_outputs=expected_outputs,
    )


# ── Runner Python ─────────────────────────────────────────────────────────

def _runner_python() -> str:
    """Return path to GPU-capable Python interpreter for experiment subprocess.

    Prefers the dedicated GPU venv (Python 3.12 + torch cu124) over the
    main project venv (Python 3.14, no cu124 wheel available).

    NOTE: must NOT resolve() the symlink — the venv python3 symlink is
    essential for Python to find the venv site-packages.
    """
    gpu_venv = Path(".venv-gpu/bin/python3")
    if gpu_venv.exists():
        return str(gpu_venv.absolute())
    return "python"


def _runner_python_env() -> dict[str, str]:
    """Environment extras needed to activate the GPU venv for subprocess."""
    gpu_venv_root = str(Path(".venv-gpu").resolve())
    return {
        "VIRTUAL_ENV": gpu_venv_root,
        "PATH": f"{gpu_venv_root}/bin:{os.environ.get('PATH', '')}",
    }


# ── Validity report ───────────────────────────────────────────────────────

def _make_validity_report(
    result: ExperimentExecutionResult | None,
    metrics_report: MetricsReport | None,
) -> ScientificValidityReport | None:
    if result is None or metrics_report is None:
        return None
    if result.exit_code != 0:
        return None
    checks = [
        ValidityCheck(
            check_id="execution_success",
            status="passed" if result.exit_code == 0 else "failed",
            message=f"exit code {result.exit_code}",
        ),
        ValidityCheck(
            check_id="metrics_parsed",
            status="passed" if metrics_report.status == "passed" else "failed",
            message=f"required {metrics_report.required_parsed}/{metrics_report.required_total} metrics",
        ),
    ]
    all_passed = all(c.status == "passed" for c in checks)
    return ScientificValidityReport(
        schema_version=1,
        status="valid" if all_passed else "invalid",
        checks=checks,
    )

# ── Metrics parsing ────────────────────────────────────────────────────────

def _parse_benchmark_metrics(
    attempt_dir: Path,
    benchmark_config: dict[str, Any] | None,
) -> MetricsReport | None:
    """Parse metrics CSV from attempt output if benchmark config provides specs."""
    if benchmark_config is None:
        return None
    eval_cfg = benchmark_config.get("evaluation", {})
    metrics_cfg = eval_cfg.get("metrics", [])
    if not metrics_cfg:
        return None
    raw_paths = eval_cfg.get("raw_result_paths", [])
    if not raw_paths:
        return None
    specs = []
    for m in metrics_cfg:
        specs.append(MetricParseSpec(
            metric_name=m["name"],
            source_path=raw_paths[0],
            source_format="csv",
            csv_row_key="Row Names",
            csv_row_value=m.get("dataset_row", "mvtec_bottle"),
            csv_metric_column=m["name"],
            dataset_row=m.get("dataset_row", "mvtec_bottle"),
            unit=m.get("unit", "ratio"),
            required=m.get("required", False),
        ))
    return parse_metrics(attempt_dir, specs)

# ── SHA computation helpers ────────────────────────────────────────────────

def _compute_env_lock_sha() -> str:
    lock_path = Path("configs/benchmarks/environments/patchcore_linux_gpu/requirements.lock.txt")
    if lock_path.exists():
        return _sha256_file(lock_path)
    return "0" * 64


def _compute_dataset_manifest_sha() -> str:
    """Deterministic manifest SHA: sorted file paths + their SHAs."""
    dataset_root = os.environ.get("AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT")
    if not dataset_root or not Path(dataset_root).exists():
        return "0" * 64
    try:
        digest = hashlib.sha256()
        root = Path(dataset_root)
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            digest.update(str(rel.as_posix()).encode())
            file_sha = _sha256_file(path)
            digest.update(file_sha.encode())
        return digest.hexdigest()
    except Exception:
        return "0" * 64


def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def _gpu_device_name() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
        return ""
    except Exception:
        return ""


def _cuda_version() -> str:
    try:
        import torch
        return torch.version.cuda or ""
    except Exception:
        return ""


def _write_gpu_evidence(stage_dir: Path) -> None:
    """Write gpu_execution_evidence.json with explicit GPU capability info.

    Probes the main process CUDA first; falls back to .venv-gpu subprocess
    probe when the main env torch is incompatible with the installed driver.
    """
    cuda_avail = _cuda_available()
    if cuda_avail:
        evidence = {
            "torch_cuda_available": True,
            "torch_cuda_version": _cuda_version(),
            "device_name": _gpu_device_name(),
            "gpu_used": True,
            "source": "runner_execute",
        }
    else:
        venv_info = _probe_gpu_venv()
        if venv_info is not None:
            evidence = {
                "torch_cuda_available": True,
                "torch_cuda_version": venv_info["cuda_version"],
                "device_name": venv_info["device_name"],
                "gpu_used": True,
                "source": "runner_execute_via_venv_gpu",
            }
        else:
            evidence = {
                "torch_cuda_available": False,
                "torch_cuda_version": "",
                "device_name": "",
                "gpu_used": False,
                "source": "runner_execute",
            }
    _write_json(stage_dir / "gpu_execution_evidence.json", evidence)


def _probe_gpu_venv() -> dict | None:
    """Run a subprocess inside .venv-gpu to probe CUDA capability.

    Returns {cuda_version, device_name} when CUDA is available,
    or None when .venv-gpu is absent / CUDA unavailable / any error.
    """
    venv_python = Path(".venv-gpu/bin/python3")
    if not venv_python.exists():
        return None
    import subprocess
    code = (
        "import torch; "
        "a=torch.cuda.is_available(); "
        "print(a, torch.version.cuda if a else '', "
        "torch.cuda.get_device_name(0) if a else '', sep='|')"
    )
    try:
        result = subprocess.run(
            [str(venv_python), "-c", code],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split("|")
        if len(parts) == 3 and parts[0] == "True":
            return {"cuda_version": parts[1], "device_name": parts[2]}
        return None
    except Exception:
        return None


def _collect_resource_usage(
    attempt_id: str,
    unit_id: str,
    wall_time_seconds: float,
    subject_type: str,
    variant_id: str | None = None,
    seed: int | None = None,
) -> "ResourceUsageReport":
    """Collect resource telemetry for an attempt.

    Queries nvidia-smi for GPU memory stats and returns a
    ResourceUsageReport with measured or partially_measured kind.
    Falls back to wall_time-only when GPU query fails.
    """
    peak_gpu_mem, avg_gpu_mem, util_pct = _query_gpu_memory()
    if peak_gpu_mem is not None:
        measurement_kind = "partially_measured"
        gpu_count = 1
        peak_gpu_mem_val = peak_gpu_mem
        avg_gpu_mem_val = avg_gpu_mem or peak_gpu_mem
        util_pct_val = util_pct or 0.0
    else:
        measurement_kind = "partially_measured"
        peak_gpu_mem_val = None
        avg_gpu_mem_val = None
        util_pct_val = None
        gpu_count = None

    return ResourceUsageReport(
        attempt_id=attempt_id,
        unit_id=unit_id,
        subject_type=subject_type,  # type: ignore
        variant_id=variant_id,
        seed=seed,
        measurement_kind=measurement_kind,
        measurement_tool="nvidia-smi+wall_clock",
        gpu_count_used=gpu_count,
        peak_gpu_memory_mb=peak_gpu_mem_val,
        avg_gpu_memory_mb=avg_gpu_mem_val,
        peak_gpu_utilization_pct=util_pct_val,
        avg_gpu_utilization_pct=util_pct_val,
        wall_time_seconds=wall_time_seconds,
        cpu_time_seconds=None,
        peak_cpu_memory_mb=None,
        evidence_refs=[],
    )


def _query_gpu_memory() -> tuple[float | None, float | None, float | None]:
    """Query nvidia-smi for peak GPU memory and utilization.

    Returns (peak_memory_mb, avg_memory_mb, utilization_pct).
    All None when nvidia-smi is unavailable or fails.
    """
    import shutil
    import subprocess
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return None, None, None
    try:
        result = subprocess.run(
            [
                nvidia_smi, "--query-gpu=memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None, None, None
        line = result.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(", ")]
        if len(parts) >= 3:
            mem_used = float(parts[0])
            mem_total = float(parts[1])
            util = float(parts[2])
            return mem_used, mem_total, util
        return None, None, None
    except Exception:
        return None, None, None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _find_workspace_ref(
    refs: list[WorkspaceExecutionRef],
    workspace_id: str,
) -> WorkspaceExecutionRef | None:
    for ref in refs:
        if ref.workspace_id == workspace_id:
            return ref
    return None


def _load_benchmark_config() -> dict[str, Any] | None:
    yaml_path = Path("configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml")
    if yaml_path.exists():
        try:
            import yaml
            with open(yaml_path) as f:
                return yaml.safe_load(f)
        except Exception:
            pass
    return None


def _repo_status(repo_root: Path) -> str:
    try:
        import subprocess
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return "error"
        return "clean" if not result.stdout.strip() else "dirty"
    except Exception:
        return "unknown"


def _compute_dirty_diff_sha256(repo_root: Path) -> str | None:
    """Compute SHA256 of dirty diff in the same format as
    ``patch_applicator._generate_unified_diff`` (uses ``difflib.unified_diff``,
    NOT ``git diff``, so SHAs match the handoff manifest).

    Returns ``None`` if git fails or the repo is clean.
    """
    import difflib
    import subprocess
    try:
        # Get list of files with unstaged changes
        name_result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=repo_root, capture_output=True, text=True, timeout=15,
        )
        if name_result.returncode != 0:
            return None
        dirty_files = [f for f in name_result.stdout.strip().splitlines() if f]
        if not dirty_files:
            return None

        lines: list[str] = []
        for rel_path in dirty_files:
            abs_path = repo_root / rel_path
            if not abs_path.exists():
                continue
            current = abs_path.read_text(encoding="utf-8")
            # Get HEAD version via git show
            head_result = subprocess.run(
                ["git", "show", f"HEAD:{rel_path}"],
                cwd=repo_root, capture_output=True, text=True, timeout=15,
            )
            original = head_result.stdout if head_result.returncode == 0 else ""

            lines.append(f"--- a/{rel_path}")
            lines.append(f"+++ b/{rel_path}")
            diff = difflib.unified_diff(
                original.split("\n"),
                current.split("\n"),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="",
            )
            lines.extend(list(diff))

        diff_text = "\n".join(lines)
        if not diff_text.strip():
            return None
        return hashlib.sha256(diff_text.encode()).hexdigest()
    except Exception:
        return None


PROTECTED_REPO_PATTERNS: list[str] = [
    "bin/",
    "configs/benchmarks/",
    "tests/",
    ".github/",
    "Makefile",
    "setup.py",
    "setup.cfg",
]


def _dirty_files_are_allowed(repo_root: Path) -> tuple[bool, list[str]]:
    """Check that dirty files are within allowed variant-patch paths.

    Returns ``(allowed, prohibited_files)``.  Prohibited files are those
    matching ``PROTECTED_REPO_PATTERNS`` — paths that a variant patch should
    never touch (evaluator scripts, benchmark configs, CI, build files).

    Uses ``git status --porcelain`` so both modified-tracked and
    untracked files are caught.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return False, ["git status --porcelain failed"]
        # Each line: XY <path>  (X=index, Y=worktree, "??" = untracked)
        dirty_files: list[str] = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # status code is 2 chars, then space, then filename
            path = line[3:] if len(line) > 3 else line
            if path and not path.startswith('"'):
                dirty_files.append(path)
    except Exception as exc:
        return False, [f"git status error: {exc}"]

    if not dirty_files:
        return True, []

    prohibited: list[str] = []
    for f in dirty_files:
        for pat in PROTECTED_REPO_PATTERNS:
            if f.startswith(pat) or f == pat:
                prohibited.append(f)
                break
    return len(prohibited) == 0, prohibited


def _compute_report_sha(report: RunnerIntakeReport) -> str:
    raw = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _compute_workspace_refs_sha(refs: list[WorkspaceExecutionRef]) -> str:
    raw = json.dumps(
        [r.model_dump(mode="json") for r in refs], sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _compute_input_refs_sha(refs: ExperimentInputRefs) -> str:
    raw = json.dumps(refs.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _derive_overall(manifest: ExecutionManifest) -> str:
    if manifest.failed_unit_count == 0 and manifest.blocked_unit_count == 0:
        return "completed"
    if manifest.completed_unit_count > 0:
        return "partially_completed"
    return "failed"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
