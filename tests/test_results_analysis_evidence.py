"""Audit: results_analysis evidence chain integration test.

Verifies:
- No-op patch → ScientificConclusion.INCOMPLETE (not PRACTICALLY_EQUIVALENT)
- Paired observation baseline ref points to correct baseline unit (by seed)
- metric_ref.sha256 == actual metrics_report.json sha256 when available
- validity_ref.sha256 == actual validity_report.json sha256 when available
- _unavailable_ref sentinel used only for genuinely absent evidence
- No placeholder SHA from hash(unit_id + "_" + role)
"""

import hashlib
import json
from pathlib import Path

import pytest

from autoad_researcher.schemas.execution import (
    ExecutionManifest,
    ExecutionUnitRecord,
    ExecutionUnitStatus,
    ExperimentExecutionHandoff,
)
from autoad_researcher.schemas.experiment_planning import ScientificConclusion
from autoad_researcher.schemas.results_analysis import (
    PairedMetricObservation,
    Reflection,
)


RUN_ID = "run_l3_bottle_001"
RUNS_ROOT = Path("runs")


def _load_manifest(runs_root: Path, run_id: str) -> ExecutionManifest:
    path = runs_root / run_id / "runner_execute" / "execution_manifest.json"
    if not path.exists():
        pytest.skip(f"no execution manifest at {path}")
    with path.open() as f:
        return ExecutionManifest.model_validate(json.load(f))


def _load_reflection(runs_root: Path, run_id: str) -> Reflection | None:
    path = runs_root / run_id / "results_analysis" / "reflection.json"
    if not path.exists():
        return None
    with path.open() as f:
        return Reflection.model_validate(json.load(f))


def _is_sentinel(ref) -> bool:
    """Check if an ArtifactReferenceV2 is the sentinel unavailable ref."""
    return ref.locator == "absent" and ref.sha256 == "0" * 64


def _resolve_artifact(ref) -> Path | None:
    if _is_sentinel(ref):
        return None
    candidate = RUNS_ROOT / ref.locator
    if candidate.exists():
        return candidate
    candidate = RUNS_ROOT / RUN_ID / ref.locator
    if candidate.exists():
        return candidate
    return None


class TestResultsAnalysisEvidenceAudit:
    """Evidence chain audit for 3.9 results analysis."""

    manifest: ExecutionManifest

    @classmethod
    def setup_class(cls):
        cls.manifest = _load_manifest(RUNS_ROOT, RUN_ID)

    # ── No-op patch conclusion ──────────────────────────────────────────

    def test_noop_conclusion_is_incomplete(self):
        """A no-op patch must yield INCOMPLETE, not PRACTICALLY_EQUIVALENT."""
        reflection = _load_reflection(RUNS_ROOT, RUN_ID)
        if reflection is None:
            pytest.skip("no reflection.json for this run")
        for vc in reflection.per_variant_conclusions:
            if vc.matched_rule_id == "noop_patch_no_scientific_claim":
                assert vc.conclusion == ScientificConclusion.INCOMPLETE, (
                    f"no-op conclusion should be INCOMPLETE, got {vc.conclusion}"
                )

    def test_noop_conclusion_not_practically_equivalent(self):
        """'practically_equivalent' must not appear in a no-op conclusion."""
        reflection = _load_reflection(RUNS_ROOT, RUN_ID)
        if reflection is None:
            pytest.skip("no reflection.json for this run")
        for vc in reflection.per_variant_conclusions:
            if vc.matched_rule_id == "noop_patch_no_scientific_claim":
                assert vc.conclusion != ScientificConclusion.PRACTICALLY_EQUIVALENT, (
                    f"no-op conclusion must not be practically_equivalent"
                )

    # ── Evidence ref integrity ─────────────────────────────────────────

    def test_metrics_report_ref_file_exists(self):
        """Every non-sentinel metrics_report_ref must point to an existing file."""
        for rec in self.manifest.unit_records:
            for attempt in rec.attempts:
                if attempt.metrics_report_ref is None:
                    continue
                ref = attempt.metrics_report_ref
                if _is_sentinel(ref):
                    continue
                file_path = _resolve_artifact(ref)
                assert file_path is not None and file_path.exists(), (
                    f"metrics_report_ref points to non-existent file: {ref.locator}"
                )

    def test_metrics_report_ref_sha_matches_file(self):
        """metrics_report_ref.sha256 must match the actual file on disk."""
        for rec in self.manifest.unit_records:
            for attempt in rec.attempts:
                if attempt.metrics_report_ref is None:
                    continue
                ref = attempt.metrics_report_ref
                file_path = _resolve_artifact(ref)
                if file_path is None:
                    continue  # checked in test_metrics_report_ref_file_exists
                actual_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
                assert ref.sha256 == actual_sha, (
                    f"metrics_report_ref SHA mismatch for {ref.locator}: "
                    f"ref says {ref.sha256}, file has {actual_sha}"
                )

    def test_validity_report_ref_file_exists(self):
        """Every non-sentinel validity_report_ref must point to an existing file."""
        for rec in self.manifest.unit_records:
            for attempt in rec.attempts:
                if attempt.validity_report_ref is None:
                    continue
                ref = attempt.validity_report_ref
                if _is_sentinel(ref):
                    continue
                file_path = _resolve_artifact(ref)
                assert file_path is not None and file_path.exists(), (
                    f"validity_report_ref points to non-existent file: {ref.locator}"
                )

    def test_validity_report_ref_sha_matches_file(self):
        """validity_report_ref.sha256 must match the actual file on disk."""
        for rec in self.manifest.unit_records:
            for attempt in rec.attempts:
                if attempt.validity_report_ref is None:
                    continue
                ref = attempt.validity_report_ref
                file_path = _resolve_artifact(ref)
                if file_path is None:
                    continue
                actual_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
                assert ref.sha256 == actual_sha, (
                    f"validity_report_ref SHA mismatch for {ref.locator}: "
                    f"ref says {ref.sha256}, file has {actual_sha}"
                )

    def test_no_placeholder_sha_in_evidence_refs(self):
        """No evidence ref may use a placeholder SHA derived from unit_id+role."""
        refs_seen = 0
        placeholder_count = 0
        for rec in self.manifest.unit_records:
            for attempt in rec.attempts:
                for ref in (attempt.metrics_report_ref, attempt.validity_report_ref):
                    if ref is None:
                        continue
                    refs_seen += 1
                    unit_id = attempt.unit_id
                    for role in ("metrics", "validity"):
                        placeholder = hashlib.sha256(f"{unit_id}_{role}".encode()).hexdigest()
                        if ref.sha256 == placeholder:
                            placeholder_count += 1
        if refs_seen > 0:
            assert placeholder_count == 0, (
                f"{placeholder_count}/{refs_seen} evidence refs use placeholder SHAs"
            )

    def test_unavailable_ref_sentinel_structure(self):
        """Sentinel refs must have locator='absent' and sha256=64 zeros."""
        from autoad_researcher.pipeline.results_analysis_stage import _unavailable_ref
        sentinel = _unavailable_ref("test_artifact")
        assert sentinel.locator == "absent"
        assert sentinel.sha256 == "0" * 64
        assert sentinel.artifact_type == "not_available"

    # ── Conclusion logic ────────────────────────────────────────────────

    def test_noop_check_function_handoff(self):
        """_check_noop_patch returns True/False based on handoff content.

        run_l3_bottle_001 has patch_diff_sha256 set but before/after both None,
        so _check_noop_patch returns False (no-op must be detected from
        metric deltas, not from ambiguous handoff fields).
        """
        from autoad_researcher.pipeline.results_analysis_stage import _check_noop_patch
        run_dir = RUNS_ROOT / RUN_ID
        handoff_path = run_dir / "patch_applicator" / "patch_runner_handoff.json"
        if not handoff_path.exists():
            pytest.skip("no PatchRunnerHandoff for this run")
        result = _check_noop_patch(run_dir)
        # before/after are both None — not enough to declare no-op from handoff alone
        assert result is False, (
            "run_l3_bottle_001 has patch_diff_sha256 set with before/after=None; "
            "_check_noop_patch should return False (None==None guard)"
        )

    # ── Paired observation baseline ref correctness ─────────────────────

    def test_paired_observation_baseline_unit_by_seed(self):
        """Each PairedMetricObservation must reference the correct baseline unit for its seed."""
        reflection = _load_reflection(RUNS_ROOT, RUN_ID)
        if reflection is None:
            pytest.skip("no reflection.json for this run")
        ac_path = RUNS_ROOT / RUN_ID / "results_analysis" / "aggregated_comparisons.json"
        if not ac_path.exists():
            pytest.skip("no aggregated_comparisons.json")
        with ac_path.open() as f:
            comparisons = json.load(f)
        for comp in comparisons:
            for obs_data in comp.get("paired_observations", []):
                obs = PairedMetricObservation.model_validate(obs_data)
                seed = obs.seed
                baseline_unit_id = obs.baseline_source.unit_id
                matching_baselines = [
                    r for r in self.manifest.unit_records
                    if getattr(r, "stage", None) == "baseline"
                    and r.seed == seed
                ]
                if not matching_baselines:
                    continue
                expected_base_id = matching_baselines[0].unit_id
                assert baseline_unit_id == expected_base_id, (
                    f"seed={seed}: PairedMetricObservation baseline unit_id="
                    f"'{baseline_unit_id}' but expected '{expected_base_id}'"
                )

    def test_paired_observation_variant_refs_are_not_placeholder(self):
        """Variant metric/validity refs must not use placeholder SHAs."""
        reflection = _load_reflection(RUNS_ROOT, RUN_ID)
        if reflection is None:
            pytest.skip("no reflection.json for this run")
        ac_path = RUNS_ROOT / RUN_ID / "results_analysis" / "aggregated_comparisons.json"
        if not ac_path.exists():
            pytest.skip("no aggregated_comparisons.json")
        with ac_path.open() as f:
            comparisons = json.load(f)
        bad = 0
        total = 0
        for comp in comparisons:
            for obs_data in comp.get("paired_observations", []):
                obs = PairedMetricObservation.model_validate(obs_data)
                vu_id = obs.variant_unit_id
                for role, ref in [("metrics", obs.variant_metric_ref),
                                  ("validity", obs.variant_validity_ref)]:
                    total += 1
                    placeholder = hashlib.sha256(f"{vu_id}_{role}".encode()).hexdigest()
                    if ref.sha256 == placeholder:
                        bad += 1
        if total > 0:
            assert bad == 0, f"{bad}/{total} variant refs use placeholder SHAs"
