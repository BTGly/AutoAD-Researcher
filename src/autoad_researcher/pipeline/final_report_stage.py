"""Stage 3.10 final_report — consolidate pipeline, execution, and scientific claims."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.schemas.execution import (
    ExecutionUnitStatus,
)
from autoad_researcher.schemas.results_analysis import (
    Reflection,
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


def _check_noop_patch(run_dir: Path) -> bool:
    """Check if the patch was a no-op (copied from results_analysis_stage)."""
    handoff_path = run_dir / "patch_applicator" / "patch_runner_handoff.json"
    if not handoff_path.exists():
        return False
    try:
        data = json.loads(handoff_path.read_text(encoding="utf-8"))
        variants = data.get("variant_workspaces", [])

        def _is_noop(vw: dict) -> bool:
            diff = vw.get("patch_diff_sha256")
            if diff is None or diff == "" or diff == "0" * 64:
                return True
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


def _detect_gpu_evidence(run_dir: Path) -> dict | None:
    """Read explicit GPU execution evidence written by 3.8 runner_execute.

    Returns the evidence dict when available, or None if no evidence file
    exists (caller must treat this as *not verified*, not as GPU completed).
    """
    evidence_path = run_dir / "runner_execute" / "gpu_execution_evidence.json"
    if not evidence_path.exists():
        return None
    try:
        return json.loads(evidence_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _stage_summary(status: str | None) -> str:
    return {
        "passed": "✅",
        "blocked": "🔴",
        "failed": "❌",
    }.get(status or "unknown", "⬜")


def run_final_report_stage(
    run_id: str,
    run_dir: Path,
    stage_dir: Path,
) -> Stage3AcceptanceStageRecord:
    """Run the 3.10 final report stage.

    Consumes 3.9 reflection + results_analysis_report + upstream artifacts →
    produces consolidated final report with 3 claim blocks.
    """
    handoff_path = stage_dir / "final_report_handoff.json"
    if handoff_path.exists():
        handoff_sha = _sha256_file(handoff_path)
        return Stage3AcceptanceStageRecord(
            stage="final_report", status="passed",
            handoff_sha256=handoff_sha,
            artifacts=[
                Stage3AcceptanceArtifactRef(
                    relative_path=str(handoff_path.relative_to(run_dir)),
                    sha256=handoff_sha,
                    artifact_type="final_report_handoff",
                ),
            ],
        )

    # ── Load 3.9 reflection ────────────────────────────────────────────
    reflection_path = run_dir / "results_analysis" / "reflection.json"
    if not reflection_path.exists():
        return Stage3AcceptanceStageRecord(
            stage="final_report", status="blocked",
            blocked_reason="blocked_upstream: results_analysis/reflection.json not found",
        )
    try:
        reflection = Reflection.model_validate_json(reflection_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return Stage3AcceptanceStageRecord(
            stage="final_report", status="blocked",
            blocked_reason=f"blocked_upstream: cannot parse reflection.json: {exc}",
        )

    # ── Detect execution mode ───────────────────────────────────────────
    is_noop = _check_noop_patch(run_dir)
    gpu_evidence = _detect_gpu_evidence(run_dir)

    if gpu_evidence:
        if gpu_evidence.get("gpu_used") and gpu_evidence.get("device_name"):
            execution_mode = "gpu_verified"
            gpu_claim = "completed"
            device_name = gpu_evidence.get("device_name", "unknown")
        else:
            execution_mode = "cpu_fallback"
            gpu_claim = "not_completed"
            device_name = ""
    else:
        execution_mode = "not_verified"
        gpu_claim = "not_completed"
        device_name = ""

    # ── Detect upstream stage status ────────────────────────────────────
    # Use stage3_acceptance_manifest.json when available; fall back to
    # checking that the stage directory exists and has at least one file.
    stage_status: dict[str, str] = {}
    all_upstream_passed = False

    manifest_path = run_dir / "stage3_acceptance" / "stage3_acceptance_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest.get("stages", []):
                stage = entry.get("stage")
                status = entry.get("status")
                if stage and status and stage != "final_report":
                    stage_status[stage] = status
        except Exception:
            pass

    # Override results_analysis if it was re-run after the manifest was written
    if run_dir.joinpath("results_analysis", "reflection.json").exists():
        stage_status["results_analysis"] = "passed"

    # Fill in any missing stages by checking directory existence
    _stage_dirs: list[tuple[str, Path]] = [
        ("intake", run_dir / "input_task.yaml"),
        ("repository_intelligence", run_dir / "repository_intelligence"),
        ("paper_intelligence", run_dir / "paper_intelligence"),
        ("research_context", run_dir / "research_context"),
        ("transfer_design", run_dir / "transfer_design"),
        ("experiment_planner", run_dir / "experiment_planning"),
        ("patch_planner", run_dir / "patch_planner"),
        ("patch_applicator", run_dir / "patch_applicator"),
        ("runner_execute", run_dir / "runner_execute"),
        ("results_analysis", run_dir / "results_analysis"),
    ]
    for _name, _path in _stage_dirs:
        if _name not in stage_status:
            stage_status[_name] = (
                "passed" if (_path.is_file() and _path.exists())
                else "passed" if (_path.is_dir() and any(_path.iterdir()))
                else "blocked"
            )

    all_upstream_passed = all(
        s == "passed" for s in stage_status.values()
    ) if stage_status else False

    # ── Scientific claim (from 3.9 conclusions, not re-detected) ─────
    noop_from_conclusion = any(
        str(c.matched_rule_id) == "noop_patch_no_scientific_claim"
        for c in reflection.per_variant_conclusions
    ) if reflection.per_variant_conclusions else is_noop

    if noop_from_conclusion:
        scientific_claim = "not_established"
        scientific_detail = (
            "No effective patch was applied. The pipeline validates execution "
            "and metrics plumbing but does not establish scientific improvement."
        )
    elif reflection.per_variant_conclusions:
        all_incomplete = all(
            str(c.conclusion) == "incomplete" or str(c.conclusion.value) == "incomplete"
            for c in reflection.per_variant_conclusions
        )
        all_no_observations = all(
            str(c.matched_rule_id) == "no_observations"
            for c in reflection.per_variant_conclusions
        )
        if all_no_observations or all_incomplete:
            scientific_claim = "not_established"
            scientific_detail = "No valid paired metric observations were available."
        else:
            beneficial = any(
                str(c.conclusion) == "beneficial" or str(c.conclusion.value) == "beneficial"
                for c in reflection.per_variant_conclusions
            )
            worse = any(
                str(c.conclusion) == "worse" or str(c.conclusion.value) == "worse"
                for c in reflection.per_variant_conclusions
            )
            if beneficial and not worse:
                scientific_claim = "improvement_demonstrated"
                scientific_detail = "At least one variant shows improvement."
            elif worse and not beneficial:
                scientific_claim = "regression_detected"
                scientific_detail = "At least one variant shows regression."
            else:
                scientific_claim = "mixed_or_inconclusive"
                scientific_detail = "Results are mixed or inconclusive."
    else:
        scientific_claim = "not_established"
        scientific_detail = "No variant conclusions available."

    # ──
    # ── Build facts ────────────────────────────────────────────────────
    report_facts = {
        "run_id": run_id,
        "all_upstream_stages_passed": all_upstream_passed,
        "scientific_claim": scientific_claim,
        "scientific_detail": scientific_detail,
        "execution_mode": execution_mode,
        "l3_gpu_claim": gpu_claim,
        "pipeline_stages": stage_status,
        "per_variant_conclusions": [
            c.model_dump(mode="json") for c in (reflection.per_variant_conclusions or [])
        ],
        "noop_patch": is_noop,
        "gpu_evidence_found": gpu_evidence is not None,
        "gpu_device_name": device_name,
        "total_units": reflection.report_facts.num_failed + reflection.report_facts.num_successful if reflection.report_facts else 0,
        "num_variants": reflection.report_facts.num_variants if reflection.report_facts else 0,
    }

    # ── Generate final_report.md ───────────────────────────────────────
    lines: list[str] = []
    lines.append(f"# AutoAD Final Report — {run_id}")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    lines.append("## 1. Engineering Pipeline Status")
    lines.append("")
    for name in stage_status:
        status = stage_status.get(name, "unknown")
        lines.append(f"  {_stage_summary(status)} **{name}**: {status}")
    lines.append("")
    lines.append(f"**All upstream stages passed:** {'Yes' if all_upstream_passed else 'No'}")

    lines.append("")
    lines.append("## 2. Execution Benchmark Status")
    lines.append("")
    lines.append(f"- Execution mode: **{execution_mode}**")
    lines.append(f"- GPU L3 claim: **{gpu_claim}**")
    if execution_mode == "cpu_fallback":
        lines.append("- ⚠️ CPU fallback was used — GPU execution not verified.")
    elif execution_mode == "not_verified":
        lines.append("- ⚠️ No GPU execution evidence found — GPU claim cannot be verified.")
    elif execution_mode == "gpu_verified" and device_name:
        lines.append(f"- GPU device: **{device_name}**")
    if reflection.report_facts:
        facts = reflection.report_facts
        lines.append(f"- Units: {facts.num_successful} successful, {facts.num_failed} failed")
        lines.append(f"- Variants tested: {facts.num_variants}")
    lines.append("")

    lines.append("## 3. Scientific Claim Status")
    lines.append("")
    lines.append(f"- Claim: **{scientific_claim}**")
    lines.append(f"- Detail: {scientific_detail}")
    lines.append("")
    if reflection.per_variant_conclusions:
        lines.append("### Variant Conclusions")
        for vc in reflection.per_variant_conclusions:
            lines.append(f"- {vc.variant_id}: {vc.conclusion.value} (rule: {vc.matched_rule_id})")
    lines.append("")
    if is_noop:
        lines.append("> **Note:** No effective patch was applied. This run validates execution and")
        lines.append("> metrics plumbing only; it does **not** establish scientific improvement or")
        lines.append("> practical equivalence.")

    # ── Write artifacts ────────────────────────────────────────────────
    stage_dir.mkdir(parents=True, exist_ok=True)

    report_path = stage_dir / "final_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    facts_path = stage_dir / "final_report_facts.json"
    _write_json(facts_path, report_facts)

    _write_json(handoff_path, {
        "run_id": run_id,
        "report_sha256": _sha256_file(report_path),
        "facts_sha256": _sha256_file(facts_path),
        "scientific_claim": scientific_claim,
        "execution_mode": execution_mode,
        "gpu_claim": gpu_claim,
        "gpu_evidence_found": gpu_evidence is not None,
        "gpu_device_name": device_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    handoff_sha = _sha256_file(handoff_path)
    artifacts = [
        Stage3AcceptanceArtifactRef(
            relative_path=str(handoff_path.relative_to(run_dir)),
            sha256=handoff_sha, artifact_type="final_report_handoff",
        ),
        Stage3AcceptanceArtifactRef(
            relative_path=str(report_path.relative_to(run_dir)),
            sha256=_sha256_file(report_path),
            artifact_type="final_report_md",
        ),
        Stage3AcceptanceArtifactRef(
            relative_path=str(facts_path.relative_to(run_dir)),
            sha256=_sha256_file(facts_path),
            artifact_type="final_report_facts",
        ),
    ]
    return Stage3AcceptanceStageRecord(
        stage="final_report", status="passed",
        handoff_sha256=handoff_sha, artifacts=artifacts,
    )
