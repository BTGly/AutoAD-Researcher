"""Stage 3.9 results_analysis — metrics comparison, scientific conclusion, resource audit."""

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.execution import (
    ExecutionManifest,
    ExecutionUnitRecord,
    ExecutionUnitStatus,
    ExperimentExecutionHandoff,
)
from autoad_researcher.schemas.experiment_planning import ScientificConclusion
from autoad_researcher.schemas.results_analysis import (
    AggregatedMetricComparison,
    AggregatedMetricKey,
    BaselineResourceAggregate,
    BundleBudgetAssessment,
    BundleResourceAggregate,
    CurrentRunBaselineMetricRef,
    EvidenceSufficiency,
    FailureAnalysis,
    PairedMetricObservation,
    Reflection,
    ReportFacts,
    ResourceComparisonReport,
    ResourceDelta,
    VariantBudgetAssessment,
    VariantResourceAggregate,
    VariantScientificConclusion,
)
from autoad_researcher.schemas.stage3_acceptance import (
    Stage3AcceptanceArtifactRef,
    Stage3AcceptanceStageRecord,
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _try_resolve(run_dir: Path, locator: str) -> Path | None:
    """Try to resolve an artifact locator, which may be relative to run_dir or runs_root."""
    candidate = run_dir / locator
    if candidate.exists():
        return candidate.resolve()
    candidate2 = run_dir.parent / locator
    if candidate2.exists():
        return candidate2.resolve()
    return None


def _read_metrics_from_csv(attempt_dir: Path) -> dict[str, float] | None:
    """Read instance_auroc and other metrics from a results CSV in the attempt outputs."""
    csv_path = attempt_dir / "outputs" / "autoad_internal_benchmark" / "internal_patchcore_mvtec_bottle_v1" / "results.csv"
    if not csv_path.exists():
        return None
    text = csv_path.read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        row_name = row.get("Row Names", "")
        if row_name == "mvtec_bottle" or row_name == "Mean":
            metrics = {}
            for key, val in row.items():
                if key == "Row Names":
                    continue
                try:
                    metrics[key] = float(val)
                except (ValueError, TypeError):
                    continue
            return metrics
    return None


def run_results_analysis_stage(
    run_id: str,
    run_dir: Path,
    stage_dir: Path,
) -> Stage3AcceptanceStageRecord:
    """Run the 3.9 results analysis stage.

    Consumes ExperimentExecutionHandoff (3.8) + ExecutionManifest →
    produces paired metric comparisons, resource audit, scientific conclusions.
    """
    handoff_path = stage_dir / "results_analysis_handoff.json"
    if handoff_path.exists():
        handoff_sha = _sha256_file(handoff_path)
        return Stage3AcceptanceStageRecord(
            stage="results_analysis", status="passed",
            handoff_sha256=handoff_sha,
            artifacts=[
                Stage3AcceptanceArtifactRef(
                    relative_path=str(handoff_path.relative_to(run_dir)),
                    sha256=handoff_sha,
                    artifact_type="results_analysis_handoff",
                ),
            ],
        )

    # ── Load 3.8 ExperimentExecutionHandoff ────────────────────────────
    handoff_38_path = run_dir / "runner_execute" / "experiment_execution_handoff.json"
    if not handoff_38_path.exists():
        return Stage3AcceptanceStageRecord(
            stage="results_analysis", status="blocked",
            blocked_reason="blocked_upstream: experiment_execution_handoff.json not found",
        )
    handoff_38 = ExperimentExecutionHandoff.model_validate_json(
        handoff_38_path.read_text(encoding="utf-8"),
    )

    # ── Load ExecutionManifest ─────────────────────────────────────────
    manifest_path = _try_resolve(run_dir, handoff_38.execution_manifest_ref.locator)
    if manifest_path is None:
        return Stage3AcceptanceStageRecord(
            stage="results_analysis", status="blocked",
            blocked_reason=f"blocked_upstream: ExecutionManifest not found at {handoff_38.execution_manifest_ref.locator}",
        )
    try:
        manifest = ExecutionManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return Stage3AcceptanceStageRecord(
            stage="results_analysis", status="blocked",
            blocked_reason=f"blocked_upstream: cannot parse ExecutionManifest: {exc}",
        )

    # ── Identify baseline and variant units ────────────────────────────
    baseline_units: list[ExecutionUnitRecord] = []
    variant_units: list[ExecutionUnitRecord] = []
    for rec in manifest.unit_records:
        stage_label = getattr(rec, "stage", None) or ""
        if stage_label == "baseline":
            baseline_units.append(rec)
        else:
            variant_units.append(rec)

    # ── Read metric values from CSV outputs ────────────────────────────
    def _unit_metrics(unit: ExecutionUnitRecord) -> dict[str, float] | None:
        if unit.final_status != ExecutionUnitStatus.COMPLETED or not unit.attempts:
            return None
        for attempt in unit.attempts:
            exec_ref = attempt.execution_result_ref
            if exec_ref is None:
                continue
            attempt_dir = _try_resolve(run_dir, exec_ref.locator)
            if attempt_dir is None:
                continue
            attempt_dir = attempt_dir.parent  # locator points to execution_result.json
            metrics = _read_metrics_from_csv(attempt_dir)
            if metrics is not None:
                return metrics
        return None

    base_metrics_map: dict[int, dict[str, float]] = {}
    for bu in baseline_units:
        m = _unit_metrics(bu)
        if m is not None:
            base_metrics_map[bu.seed] = m

    variant_info: list[dict] = []
    for vu in variant_units:
        m = _unit_metrics(vu)
        variant_info.append({
            "unit": vu,
            "metrics": m,
            "variant_id": getattr(vu, "variant_id", vu.unit_id),
            "seed": vu.seed,
        })

    # ── Paired metric observations ─────────────────────────────────────
    paired_observations: list[PairedMetricObservation] = []
    seen_pairs: set[tuple[str, int, str]] = set()
    METRIC_NAME = "instance_auroc"
    DIRECTION = "maximize"

    for vi in variant_info:
        vu = vi["unit"]
        v_seed = vi["seed"]
        v_metrics = vi["metrics"]
        v_id = vi["variant_id"]
        if v_metrics is None:
            continue

        base_metrics = base_metrics_map.get(v_seed)
        if base_metrics is None:
            continue

        base_val = base_metrics.get(METRIC_NAME)
        var_val = v_metrics.get(METRIC_NAME)
        if base_val is None or var_val is None:
            continue

        pair_key = (v_id, v_seed, METRIC_NAME)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        raw_delta = var_val - base_val
        improvement_delta = raw_delta if DIRECTION == "maximize" else base_val - var_val
        abs_base = abs(base_val)
        raw_rel = raw_delta / abs_base * 100.0 if abs_base > 1e-10 else None
        imp_rel = improvement_delta / abs_base * 100.0 if abs_base > 1e-10 else None

        obs = PairedMetricObservation(
            seed=v_seed,
            baseline_source=CurrentRunBaselineMetricRef(
                metric_name=METRIC_NAME,
                unit_id=baseline_units[0].unit_id if baseline_units else "unknown",
                seed=v_seed,
                metric_ref=self_ref("metrics", vu, run_id),
                validity_ref=self_ref("validity", vu, run_id),
            ),
            baseline_value=base_val,
            variant_unit_id=vu.unit_id,
            variant_id=v_id,
            variant_metric_ref=self_ref("metrics", vu, run_id),
            variant_value=var_val,
            direction=DIRECTION,
            raw_delta=raw_delta,
            improvement_delta=improvement_delta,
            raw_relative_change_pct=raw_rel,
            improvement_relative_change_pct=imp_rel,
            pair_validity_status="valid",
            variant_validity_ref=self_ref("validity", vu, run_id),
            baseline_validity_ref=self_ref("validity", baseline_units[0], run_id) if baseline_units else self_ref("validity", vu, run_id),
            protocol_fingerprint=handoff_38.protocol_fingerprint,
        )
        paired_observations.append(obs)

    # ── Build variant role list from actual data ───────────────────────
    seen_variant_ids: list[str] = []
    for vi in variant_info:
        vid = vi["variant_id"]
        if vid not in seen_variant_ids:
            seen_variant_ids.append(vid)

    # ── Aggregated comparisons ─────────────────────────────────────────
    aggregated_comparisons: list[AggregatedMetricComparison] = []
    for v_id in seen_variant_ids:
        v_obs = [o for o in paired_observations if o.variant_id == v_id]
        if not v_obs:
            aggregated_comparisons.append(AggregatedMetricComparison(
                aggregate_key=AggregatedMetricKey(
                    variant_id=v_id, metric_name=METRIC_NAME,
                    dataset_row="mvtec_bottle", direction=DIRECTION,
                ),
                comparison_status="missing",
                seed_count=0,
                completed_seed_count=0,
            ))
            continue
        mean_base = sum(o.baseline_value for o in v_obs) / len(v_obs)
        mean_var = sum(o.variant_value for o in v_obs) / len(v_obs)
        mean_delta = sum(o.raw_delta for o in v_obs) / len(v_obs)
        mean_imp = sum(o.improvement_delta for o in v_obs) / len(v_obs)
        aggregated_comparisons.append(AggregatedMetricComparison(
            aggregate_key=AggregatedMetricKey(
                variant_id=v_id, metric_name=METRIC_NAME,
                dataset_row="mvtec_bottle", direction=DIRECTION,
            ),
            paired_observations=v_obs,
            comparison_status="valid" if all(o.pair_validity_status == "valid" for o in v_obs) else "degraded",
            seed_count=len(v_obs),
            completed_seed_count=len(v_obs),
            mean_baseline=mean_base,
            mean_variant=mean_var,
            mean_raw_delta=mean_delta,
            mean_improvement_delta=mean_imp,
        ))

    # ── Evidence sufficiency ───────────────────────────────────────────
    evidence_sufficiency: list[EvidenceSufficiency] = []
    for v_id in seen_variant_ids:
        v_units = [vi["unit"] for vi in variant_info if vi["variant_id"] == v_id]
        v_obs = [o for o in paired_observations if o.variant_id == v_id]
        valid_obs = [o for o in v_obs if o.pair_validity_status == "valid"]
        evidence_sufficiency.append(EvidenceSufficiency(
            variant_id=v_id,
            total_planned_seeds=len(v_units),
            completed_seed_pairs=len(v_obs),
            valid_seed_pairs=len(valid_obs),
            metric_count=len(v_obs),
            valid_metric_count=len(valid_obs),
            protocol_fingerprint=handoff_38.protocol_fingerprint,
        ))

    # ── Resource comparison ────────────────────────────────────────────
    baseline_resource = BaselineResourceAggregate(measurement_status="not_available")
    per_variant_resource: dict[str, VariantResourceAggregate] = {}
    per_variant_deltas: dict[str, ResourceDelta] = {}
    per_variant_budget: dict[str, VariantBudgetAssessment] = {}
    for v_id in seen_variant_ids:
        per_variant_resource[v_id] = VariantResourceAggregate(
            variant_id=v_id, measurement_status="not_available",
        )
        per_variant_deltas[v_id] = ResourceDelta(
            variant_id=v_id, measurement_compatible=False,
        )
        per_variant_budget[v_id] = VariantBudgetAssessment(
            variant_id=v_id, status="not_assessable",
            reason="resource telemetry not available",
        )
    bundle_resource = BundleResourceAggregate(
        baseline=baseline_resource, per_variant=per_variant_resource,
    )
    bundle_budget = BundleBudgetAssessment(
        status="not_assessable", reason="resource telemetry not available",
    )
    resource_report = ResourceComparisonReport(
        baseline=baseline_resource, per_variant=per_variant_resource,
        per_variant_deltas=per_variant_deltas,
        per_variant_budget_assessments=per_variant_budget,
        bundle=bundle_resource, bundle_budget_assessment=bundle_budget,
    )

    # ── Report facts ───────────────────────────────────────────────────
    completed = handoff_38.completed_unit_ids
    failed = handoff_38.failed_unit_ids
    report_facts = ReportFacts(
        run_id=run_id,
        num_variants=len(seen_variant_ids),
        num_successful=len(completed),
        num_failed=len(failed),
        total_gpu_hours=0.0,
        total_wall_time_seconds=0.0,
    )

    # ── Scientific conclusion (no-op aware) ────────────────────────────
    no_effective_patch = _check_noop_patch(run_dir)
    per_variant_conclusions: list[VariantScientificConclusion] = []
    for v_id in seen_variant_ids:
        if no_effective_patch:
            conclusion_val = ScientificConclusion.PRACTICALLY_EQUIVALENT
            matched_rule = "noop_patch_detected"
        else:
            v_obs = [o for o in paired_observations if o.variant_id == v_id]
            if v_obs:
                avg_imp = sum(o.improvement_delta for o in v_obs) / len(v_obs)
                if avg_imp > 1e-9:
                    conclusion_val = ScientificConclusion.BENEFICIAL
                    matched_rule = "positive_improvement_delta"
                elif avg_imp < -1e-9:
                    conclusion_val = ScientificConclusion.WORSE
                    matched_rule = "negative_improvement_delta"
                else:
                    conclusion_val = ScientificConclusion.PRACTICALLY_EQUIVALENT
                    matched_rule = "zero_improvement_delta"
            else:
                conclusion_val = ScientificConclusion.INCOMPLETE
                matched_rule = "no_observations"

        per_variant_conclusions.append(VariantScientificConclusion(
            variant_id=v_id, conclusion=conclusion_val,
            matched_rule_id=matched_rule,
        ))

    # ── Failure analysis ───────────────────────────────────────────────
    failure_analysis = FailureAnalysis(
        failure_summary=f"0 failed units, {len(completed)} completed, {len(failed)} blocked" if not failed
        else f"{len(failed)} failed units",
    )

    # ── Reflection ─────────────────────────────────────────────────────
    reflection = Reflection(
        per_variant_conclusions=per_variant_conclusions,
        resource_report=resource_report,
        failure_analysis=failure_analysis,
        report_facts=report_facts,
    )

    # ── Write artifacts ────────────────────────────────────────────────
    stage_dir.mkdir(parents=True, exist_ok=True)
    _write_json(stage_dir / "reflection.json", reflection.model_dump(mode="json", exclude_none=True))
    _write_json(handoff_path, {
        "run_id": run_id,
        "reflection_sha256": _sha256_file(stage_dir / "reflection.json"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    _write_json(stage_dir / "aggregated_comparisons.json", [
        c.model_dump(mode="json", exclude_none=True) for c in aggregated_comparisons
    ])
    _write_json(stage_dir / "evidence_sufficiency.json", [
        e.model_dump(mode="json", exclude_none=True) for e in evidence_sufficiency
    ])

    report_lines: list[str] = []
    report_lines.append(f"# Results Analysis Report — {run_id}")
    report_lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    report_lines.append("")
    report_lines.append("## Summary")
    report_lines.append(f"- Variants: {len(seen_variant_ids)}")
    report_lines.append(f"- Completed units: {len(completed)}")
    report_lines.append(f"- Failed units: {len(failed)}")
    report_lines.append("")
    report_lines.append("## Scientific Conclusions")
    for vc in per_variant_conclusions:
        report_lines.append(f"- {vc.variant_id}: {vc.conclusion.value} (rule: {vc.matched_rule_id})")
    report_lines.append("")
    report_lines.append("## Paired Comparisons")
    for obs in paired_observations:
        imp_str = f"{obs.improvement_delta:+.4f} ({obs.improvement_relative_change_pct:+.2f}%)" if obs.improvement_relative_change_pct is not None else f"{obs.improvement_delta:+.4f}"
        report_lines.append(
            f"- {obs.variant_id} seed={obs.seed}: "
            f"baseline={obs.baseline_value:.4f} → variant={obs.variant_value:.4f}, "
            f"Δ={imp_str}"
        )
    report_lines.append("")
    report_lines.append("## Resource Usage")
    report_lines.append("- Resource telemetry: not available")

    report_path = stage_dir / "final_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    handoff_sha = _sha256_file(handoff_path)
    artifacts = [
        Stage3AcceptanceArtifactRef(
            relative_path=str(handoff_path.relative_to(run_dir)),
            sha256=handoff_sha, artifact_type="results_analysis_handoff",
        ),
        Stage3AcceptanceArtifactRef(
            relative_path=str((stage_dir / "reflection.json").relative_to(run_dir)),
            sha256=_sha256_file(stage_dir / "reflection.json"),
            artifact_type="reflection",
        ),
        Stage3AcceptanceArtifactRef(
            relative_path=str(report_path.relative_to(run_dir)),
            sha256=_sha256_file(report_path),
            artifact_type="final_report_md",
        ),
    ]
    return Stage3AcceptanceStageRecord(
        stage="results_analysis", status="passed",
        handoff_sha256=handoff_sha, artifacts=artifacts,
    )


def self_ref(role: str, unit: ExecutionUnitRecord, run_id: str) -> ArtifactReferenceV2:
    """Build a placeholder artifact reference for metrics or validity."""
    return ArtifactReferenceV2(
        artifact_id=f"{role}_{unit.unit_id}",
        artifact_type=role,
        locator=f"{run_id}/runner_execute/attempts/{unit.unit_id}/attempt_1/{role}.json",
        sha256=hashlib.sha256(f"{unit.unit_id}_{role}".encode()).hexdigest(),
    )


def _check_noop_patch(run_dir: Path) -> bool:
    """Check if the patch was a no-op by looking at the PatchRunnerHandoff."""
    handoff_37_path = run_dir / "patch_applicator" / "patch_runner_handoff.json"
    if not handoff_37_path.exists():
        return False
    try:
        data = json.loads(handoff_37_path.read_text(encoding="utf-8"))
        variants = data.get("variant_workspaces", [])
        return all(
            vw.get("patch_diff_sha256") is None
            or vw.get("patch_diff_sha256") == ""
            or vw.get("patch_diff_sha256") == "0" * 64
            for vw in variants
        )
    except Exception:
        return False
