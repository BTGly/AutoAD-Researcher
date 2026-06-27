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
    ResourceUsageReport,
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

    def _attempt_ref(unit: ExecutionUnitRecord, attr: str) -> ArtifactReferenceV2 | None:
        """Read metrics_report_ref or validity_report_ref from the final attempt."""
        if not unit.attempts:
            return None
        for attempt in reversed(unit.attempts):
            ref = getattr(attempt, attr, None)
            if ref is not None:
                return ref
        return None

    base_units_by_seed: dict[int, ExecutionUnitRecord] = {}
    base_metrics_map: dict[int, dict[str, float]] = {}
    for bu in baseline_units:
        m = _unit_metrics(bu)
        if m is not None and bu.seed is not None:
            base_units_by_seed[bu.seed] = bu
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
        if v_metrics is None or v_seed is None:
            continue

        base_unit = base_units_by_seed.get(v_seed)
        base_metrics = base_metrics_map.get(v_seed)
        if base_unit is None or base_metrics is None:
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

        base_metrics_ref = _attempt_ref(base_unit, "metrics_report_ref")
        base_validity_ref = _attempt_ref(base_unit, "validity_report_ref")
        var_metrics_ref = _attempt_ref(vu, "metrics_report_ref")
        var_validity_ref = _attempt_ref(vu, "validity_report_ref")

        pair_validity = "valid"
        base_missing = base_metrics_ref is None or base_validity_ref is None
        var_missing = var_metrics_ref is None or var_validity_ref is None
        if base_missing or var_missing:
            pair_validity = "insufficient_evidence"

        obs = PairedMetricObservation(
            seed=v_seed,
            baseline_source=CurrentRunBaselineMetricRef(
                metric_name=METRIC_NAME,
                unit_id=base_unit.unit_id,
                seed=v_seed,
                metric_ref=base_metrics_ref or _unavailable_ref(f"metrics_{base_unit.unit_id}"),
                validity_ref=base_validity_ref or _unavailable_ref(f"validity_{base_unit.unit_id}"),
            ),
            baseline_value=base_val,
            variant_unit_id=vu.unit_id,
            variant_id=v_id,
            variant_metric_ref=var_metrics_ref or _unavailable_ref(f"metrics_{vu.unit_id}"),
            variant_value=var_val,
            direction=DIRECTION,
            raw_delta=raw_delta,
            improvement_delta=improvement_delta,
            raw_relative_change_pct=raw_rel,
            improvement_relative_change_pct=imp_rel,
            pair_validity_status=pair_validity,
            variant_validity_ref=var_validity_ref or _unavailable_ref(f"validity_{vu.unit_id}"),
            baseline_validity_ref=base_validity_ref or _unavailable_ref(f"validity_{base_unit.unit_id}"),
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
    def _load_resource_usage(u: ExecutionUnitRecord) -> ResourceUsageReport | None:
        if not u.attempts:
            return None
        for attempt in reversed(u.attempts):
            ref = attempt.resource_usage_ref
            if ref is None:
                continue
            rsrc_path = _try_resolve(run_dir, ref.locator)
            if rsrc_path is None:
                continue
            try:
                return ResourceUsageReport.model_validate_json(rsrc_path.read_text(encoding="utf-8"))
            except Exception:
                continue
        return None

    def _agg_resource(
        units: list[ExecutionUnitRecord],
    ) -> tuple[list[ArtifactReferenceV2], dict[str, float], float | None, float | None, str]:
        refs: list[ArtifactReferenceV2] = []
        per_unit_gpu: dict[str, float] = {}
        total_wall: float = 0.0
        peak_gpu_mem: float | None = None
        any_measured = False
        for u in units:
            r = _load_resource_usage(u)
            if r is None:
                continue
            refs.append(ArtifactReferenceV2(
                artifact_id=f"resource_usage_{r.attempt_id}",
                artifact_type="resource_usage_report",
                locator="absent", sha256="0" * 64,
            ))
            per_unit_gpu[u.unit_id] = r.actual_gpu_hours or 0.0
            if r.wall_time_seconds is not None:
                total_wall += r.wall_time_seconds
            if r.peak_gpu_memory_mb is not None:
                peak_gpu_mem = max(peak_gpu_mem or 0, r.peak_gpu_memory_mb)
            if r.measurement_kind == "measured":
                any_measured = True
        status = "measured" if any_measured else ("partially_measured" if refs else "not_available")
        return refs, per_unit_gpu, total_wall if refs else None, peak_gpu_mem, status

    base_refs, base_per_unit_gpu, base_wall, base_peak, base_status = _agg_resource(baseline_units)
    baseline_resource = BaselineResourceAggregate(
        attempt_report_refs=base_refs,
        per_unit_actual_gpu_hours=base_per_unit_gpu,
        total_wall_time_seconds=base_wall,
        peak_gpu_memory_mb=base_peak,
        measurement_status=base_status,
    )
    per_variant_resource: dict[str, VariantResourceAggregate] = {}
    per_variant_deltas: dict[str, ResourceDelta] = {}
    per_variant_budget: dict[str, VariantBudgetAssessment] = {}
    for v_id in seen_variant_ids:
        v_units = [u for u in variant_units if getattr(u, "variant_id", u.unit_id) == v_id]
        v_refs, v_per_unit_gpu, v_wall, v_peak, v_status = _agg_resource(v_units)
        per_variant_resource[v_id] = VariantResourceAggregate(
            variant_id=v_id, attempt_report_refs=v_refs,
            per_unit_actual_gpu_hours=v_per_unit_gpu,
            total_wall_time_seconds=v_wall, peak_gpu_memory_mb=v_peak,
            measurement_status=v_status,
        )
        wall_delta = (v_wall - base_wall) if (v_wall is not None and base_wall is not None) else None
        mem_delta = (v_peak - base_peak) if (v_peak is not None and base_peak is not None) else None
        per_variant_deltas[v_id] = ResourceDelta(
            variant_id=v_id,
            wall_time_delta_seconds=wall_delta,
            gpu_memory_delta_mb=mem_delta,
            measurement_compatible=(v_status != "not_available" and base_status != "not_available"),
        )
        per_variant_budget[v_id] = VariantBudgetAssessment(
            variant_id=v_id, status="not_assessable",
            reason="resource budget not configured",
        )
    bundle_resource = BundleResourceAggregate(
        baseline=baseline_resource, per_variant=per_variant_resource,
    )
    bundle_budget = BundleBudgetAssessment(
        status="not_assessable", reason="resource budget not configured",
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
    total_gpu = (
        bundle_resource.baseline.total_actual_gpu_hours + bundle_resource.total_actual_gpu_hours
    ) if bundle_resource.baseline.measurement_status != "not_available" else 0.0
    total_wall = (bundle_resource.baseline.total_wall_time_seconds or 0.0)
    for v_agg in bundle_resource.per_variant.values():
        if v_agg.total_wall_time_seconds is not None:
            total_wall += v_agg.total_wall_time_seconds
    report_facts = ReportFacts(
        run_id=run_id,
        num_variants=len(seen_variant_ids),
        num_successful=len(completed),
        num_failed=len(failed),
        total_gpu_hours=total_gpu,
        total_wall_time_seconds=total_wall,
    )

    # ── Scientific conclusion (no-op aware) ────────────────────────────
    no_effective_patch = _check_noop_patch(run_dir)
    per_variant_conclusions: list[VariantScientificConclusion] = []
    for v_id in seen_variant_ids:
        if no_effective_patch:
            # No effective patch was applied — the run validates execution and
            # metrics plumbing only; it does not establish scientific improvement
            # or practical equivalence.
            conclusion_val = ScientificConclusion.INCOMPLETE
            matched_rule = "noop_patch_no_scientific_claim"
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

    # Strip computed_fields that break round-trip serialization
    def _strip_computed(obj):
        if isinstance(obj, dict):
            banned = {"total_actual_gpu_hours", "max_unit_actual_gpu_hours"}
            return {k: _strip_computed(v) for k, v in obj.items() if k not in banned}
        if isinstance(obj, list):
            return [_strip_computed(v) for v in obj]
        return obj

    # ── Write artifacts ────────────────────────────────────────────────
    stage_dir.mkdir(parents=True, exist_ok=True)
    ref_data = _strip_computed(reflection.model_dump(mode="json", exclude_none=True))
    _write_json(stage_dir / "reflection.json", ref_data)
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
    if no_effective_patch:
        report_lines.append("")
        report_lines.append("> **Note:** No effective patch was applied. This run validates execution and")
        report_lines.append("> metrics plumbing only; it does **not** establish scientific improvement or")
        report_lines.append("> practical equivalence.")
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
    if base_status != "not_available":
        report_lines.append(f"- Baseline: wall_time={base_wall:.1f}s, peak_gpu_mem={base_peak or 0:.0f}MB")
        for v_id in seen_variant_ids:
            v_agg = per_variant_resource.get(v_id)
            if v_agg and v_agg.measurement_status != "not_available":
                report_lines.append(
                    f"- {v_id}: wall_time={v_agg.total_wall_time_seconds or 0:.1f}s, "
                    f"peak_gpu_mem={v_agg.peak_gpu_memory_mb or 0:.0f}MB"
                )
                delta = per_variant_deltas.get(v_id)
                if delta and delta.measurement_compatible:
                    wd = delta.wall_time_delta_seconds
                    md = delta.gpu_memory_delta_mb
                    report_lines.append(f"  Δ wall_time={wd:+.1f}s, Δ gpu_mem={md:+.0f}MB" if wd is not None and md is not None else "")
    else:
        report_lines.append("- Resource telemetry: not available")

    report_path = stage_dir / "results_analysis_report.md"
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
            artifact_type="results_analysis_report",
        ),
    ]
    return Stage3AcceptanceStageRecord(
        stage="results_analysis", status="passed",
        handoff_sha256=handoff_sha, artifacts=artifacts,
    )


def _unavailable_ref(artifact_id: str) -> ArtifactReferenceV2:
    """Build a sentinel artifact reference for missing/absent evidence."""
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type="not_available",
        locator="absent",
        sha256="0" * 64,
    )


def _check_noop_patch(run_dir: Path) -> bool:
    """Check if the patch was a no-op by looking at the PatchRunnerHandoff.

    A patch is no-op if:
    - patch_diff_sha256 is None/empty/zero, OR
    - before_sha256 and after_sha256 are both non-empty strings and equal
      (identical content — no effective change)

    Important: None == None is NOT treated as equal-content to avoid false
    positives when before/after fields are absent in future runs.
    """
    handoff_37_path = run_dir / "patch_applicator" / "patch_runner_handoff.json"
    if not handoff_37_path.exists():
        return False
    try:
        data = json.loads(handoff_37_path.read_text(encoding="utf-8"))
        variants = data.get("variant_workspaces", [])

        def _is_noop(vw: dict) -> bool:
            # Empty diff indicator
            diff = vw.get("patch_diff_sha256")
            if diff is None or diff == "" or diff == "0" * 64:
                return True
            # Equal content check — both must be present (not None) and equal.
            # Empty strings are allowed (represent uncommitted workspaces).
            before = vw.get("before_sha256")
            after = vw.get("after_sha256")
            if (
                before is not None
                and after is not None
                and before == after
            ):
                return True
            return False

        return all(_is_noop(vw) for vw in variants)
    except Exception:
        return False
