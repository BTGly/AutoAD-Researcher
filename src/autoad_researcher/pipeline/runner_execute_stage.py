"""Stage 3.8 runner_execute — handoff intake → execution units → ExperimentExecutionHandoff."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.runner.executor import (
    execute_experiment_attempt,
    run_experiment_subprocess,
)
from autoad_researcher.runner.handoff_bridge import build_runner_intake_request
from autoad_researcher.runner.models import ExperimentCommandPlan, ExperimentInputRefs
from autoad_researcher.runner.validators import (
    derive_attempt_outcome,
    compute_identity_match,
    validate_intake_against_patch_handoff,
)
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.execution import (
    AttemptIdentitySnapshot,
    AttemptOutcome,
    AttemptRecord,
    ExecutionManifest,
    ExecutionUnitPlan,
    ExecutionUnitRecord,
    ExecutionUnitStatus,
    ExperimentExecutionHandoff,
    IntakeCheck,
    MatrixCoverageReport,
    ResourceUsageReport,
    RetryDecision,
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
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    stats_plan = json.loads(stats_path.read_text(encoding="utf-8"))
    guard_policy = json.loads(guard_path.read_text(encoding="utf-8"))

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
    benchmark_config = _load_benchmark_config(run_dir)

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
        handoff_35=handoff_35,
        handoff_37=handoff_37,
        stage_dir=stage_dir,
        benchmark_config=benchmark_config,
    )
    _write_json(stage_dir / "execution_unit_plans.json",
                [u.model_dump(mode="json") for u in units])

    # ── Build command plans and execute ──────────────────────────────────
    unit_records: list[ExecutionUnitRecord] = []
    all_attempt_records: list[AttemptRecord] = []
    retry_decisions: list[RetryDecision] = []

    for unit in units:
        unit_dir = stage_dir / "attempts" / unit.unit_id
        unit_dir.mkdir(parents=True, exist_ok=True)

        command_plan, input_refs = _build_command_plan_and_refs(
            unit=unit,
            run_id=run_id,
            workspace_ref=_find_workspace_ref(
                intake_request.workspace_refs, unit.workspace_id,
            ),
            repo_root=repo_root,
        )

        attempts: list[AttemptRecord] = []
        final_status: ExecutionUnitStatus = ExecutionUnitStatus.BLOCKED
        terminal_reason: TerminalReason = "intake_failed"

        for attempt_idx in range(1, unit.max_attempts + 1):
            attempt_id = f"{unit.unit_id}_attempt_{attempt_idx}"
            attempt_dir = unit_dir / f"attempt_{attempt_idx}"
            attempt_dir.mkdir(parents=True, exist_ok=True)

            identity = AttemptIdentitySnapshot(
                execution_unit_plan_sha256=unit.command_plan_sha256,
                command_sha256=input_refs.command_sha256,
                input_refs_sha256=_compute_input_refs_sha(input_refs),
                workspace_repository_fingerprint=(
                    _find_workspace_ref(
                        intake_request.workspace_refs, unit.workspace_id,
                    ).repository_fingerprint
                ),
            )

            result = execute_experiment_attempt(
                run_id=run_id,
                attempt=attempt_id,
                command_plan=command_plan,
                input_refs=input_refs,
                attempt_dir=str(attempt_dir),
                runner=run_experiment_subprocess,
            )

            outcome = derive_attempt_outcome(result, None, None)

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
                    prev_identity=identity,
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

    # ── Build execution command plan manifest ────────────────────────────
    _write_json(stage_dir / "experiment_command_plan.json",
                [c.model_dump(mode="json") for c in _collect_command_plans(units, run_id, intake_request, repo_root)])

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

    stage_status = "passed" if overall_status in ("completed", "partially_completed") else "blocked"

    return Stage3AcceptanceStageRecord(
        stage="runner_execute", status=stage_status,
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
    if repo_status != "clean":
        checks.append(IntakeCheck(
            name="repo_clean", status="failed",
            details=f"repo status: {repo_status}",
        ))
        return RunnerIntakeReport(
            status="blocked", checks=checks,
            report_sha256="0" * 64,
        )
    checks.append(IntakeCheck(name="repo_clean", status="passed"))

    report = RunnerIntakeReport(status="eligible", checks=checks, report_sha256="0" * 64)
    report.report_sha256 = _compute_report_sha(report)
    return report


# ── Execution unit builders ─────────────────────────────────────────────────

def _build_execution_units(
    experiment_matrix: dict[str, Any],
    handoff_35: dict[str, Any],
    handoff_37: PatchRunnerHandoff,
    stage_dir: Path,
    benchmark_config: dict[str, Any] | None,
) -> list[ExecutionUnitPlan]:
    """Build one ExecutionUnitPlan per experiment matrix entry."""
    entries = experiment_matrix.get("entries", [])
    units: list[ExecutionUnitPlan] = []

    for i, entry in enumerate(entries):
        entry_id: str = entry["entry_id"]
        variant_id: str | None = entry.get("variant_id")
        seed: int | None = entry.get("seed")

        # Determine workspace_id from variant_id
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

    # Fix command_plan_sha256 after construction
    for unit in units:
        command_plan = _make_command_plan(unit, benchmark_config, stage_dir)
        from autoad_researcher.runner.executor import experiment_command_sha256
        unit.command_plan_sha256 = experiment_command_sha256(command_plan)

    return units


def _make_command_plan(
    unit: ExecutionUnitPlan,
    benchmark_config: dict[str, Any] | None,
    stage_dir: Path,
) -> ExperimentCommandPlan:
    """Construct an ExperimentCommandPlan for a single execution unit.

    Uses the benchmark config to build the PatchCore run command.
    """
    if benchmark_config:
        entrypoint = benchmark_config.get("repository", {}).get(
            "entrypoint_path", "bin/run_patchcore.py",
        )
        repo = Path("workspace/repos/patchcore-inspection")
        raw_result_paths = benchmark_config.get("evaluation", {}).get(
            "raw_result_paths",
            ["outputs/autoad_internal_benchmark/internal_patchcore_mvtec_bottle_v1/results.csv"],
        )
        expected_outputs = raw_result_paths
    else:
        entrypoint = "bin/run_patchcore.py"
        repo = Path("workspace/repos/patchcore-inspection")
        expected_outputs = ["outputs/results.csv"]

    return ExperimentCommandPlan(
        schema_version=1,
        command_id=f"cmd_{unit.unit_id}",
        program="python",
        args=[str(repo / entrypoint)],
        cwd=str(repo),
        environment={},
        timeout_seconds=unit.max_wall_time_seconds,
        network=False,
        expected_outputs=expected_outputs,
    )


def _build_command_plan_and_refs(
    unit: ExecutionUnitPlan,
    run_id: str,
    workspace_ref: WorkspaceExecutionRef | None,
    repo_root: Path,
) -> tuple[ExperimentCommandPlan, ExperimentInputRefs]:
    plan = _make_command_plan(unit, None, Path("/tmp"))
    from autoad_researcher.runner.executor import experiment_command_sha256
    cmd_sha = experiment_command_sha256(plan)

    input_refs = ExperimentInputRefs(
        repository_fingerprint=(
            workspace_ref.repository_fingerprint if workspace_ref else "0" * 64
        ),
        environment_sha256="0" * 64,
        dataset_manifest_sha256="0" * 64,
        asset_manifest_sha256="0" * 64,
        command_sha256=cmd_sha,
    )
    return plan, input_refs


def _find_workspace_ref(
    refs: list[WorkspaceExecutionRef],
    workspace_id: str,
) -> WorkspaceExecutionRef | None:
    for ref in refs:
        if ref.workspace_id == workspace_id:
            return ref
    return None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _load_benchmark_config(run_dir: Path) -> dict[str, Any] | None:
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


def _collect_command_plans(
    units: list[ExecutionUnitPlan],
    run_id: str,
    intake_request: RunnerIntakeRequest,
    repo_root: Path,
) -> list[dict[str, Any]]:
    plans = []
    for unit in units:
        ws_ref = _find_workspace_ref(intake_request.workspace_refs, unit.workspace_id)
        plan, _ = _build_command_plan_and_refs(unit, run_id, ws_ref, repo_root)
        plans.append(plan.model_dump(mode="json"))
    return plans


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
