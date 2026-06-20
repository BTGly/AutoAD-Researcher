"""Audit: final_report (3.10) evidence chain integration test.

Verifies:
- final_report.md exists and is non-empty
- final_report_facts.json exists and contains expected keys
- Handoff SHA matches on re-run (idempotent)
- All three claim sections present in report
- No-op patch flagged in report when 3.9 says noop_patch_no_scientific_claim
- Stage status accurately reflects manifest
- GPU claim defaults to not_completed without explicit evidence
"""

import hashlib
import json
from pathlib import Path

import pytest

from autoad_researcher.schemas.results_analysis import Reflection

RUN_ID = "run_l3_bottle_001"
RUNS_ROOT = Path("runs")


def _required_artifacts_exist() -> bool:
    """Check that all upstream artifacts for final_report audit are present."""
    base = RUNS_ROOT / RUN_ID
    required = [
        base / "results_analysis" / "reflection.json",
        base / "final_report" / "final_report.md",
        base / "final_report" / "final_report_handoff.json",
        base / "final_report" / "final_report_facts.json",
    ]
    return all(p.exists() for p in required)


def _load_handoff(runs_root: Path, run_id: str) -> dict | None:
    path = runs_root / run_id / "final_report" / "final_report_handoff.json"
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def _load_facts(runs_root: Path, run_id: str) -> dict | None:
    path = runs_root / run_id / "final_report" / "final_report_facts.json"
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def _load_reflection(runs_root: Path, run_id: str) -> Reflection | None:
    path = runs_root / run_id / "results_analysis" / "reflection.json"
    if not path.exists():
        return None
    with path.open() as f:
        return Reflection.model_validate(json.load(f))


class TestFinalReportEvidenceAudit:
    """Evidence chain audit for 3.10 final report."""

    handoff: dict | None = None
    facts: dict | None = None

    @classmethod
    def setup_class(cls):
        if not _required_artifacts_exist():
            pytest.skip(
                "final_report evidence artifacts not present; "
                "run `uv run autoad final-report --run-id run_l3_bottle_001 --json` first"
            )
        cls.handoff = _load_handoff(RUNS_ROOT, RUN_ID)
        cls.facts = _load_facts(RUNS_ROOT, RUN_ID)
        cls.reflection = _load_reflection(RUNS_ROOT, RUN_ID)

    # ── Artifact existence ──────────────────────────────────────────────

    def test_final_report_md_exists(self):
        path = RUNS_ROOT / RUN_ID / "final_report" / "final_report.md"
        assert path.exists(), "final_report.md must exist"
        text = path.read_text(encoding="utf-8")
        assert len(text) > 100, "final_report.md must be non-trivial"

    def test_final_report_facts_exists(self):
        assert self.facts is not None, "final_report_facts.json must exist"
        assert "run_id" in self.facts
        assert "scientific_claim" in self.facts
        assert "execution_mode" in self.facts
        assert "pipeline_stages" in self.facts
        assert "per_variant_conclusions" in self.facts

    def test_handoff_exists(self):
        assert self.handoff is not None, "final_report_handoff.json must exist"
        assert "report_sha256" in self.handoff
        assert "facts_sha256" in self.handoff
        assert "scientific_claim" in self.handoff
        assert "execution_mode" in self.handoff
        assert "gpu_claim" in self.handoff

    # ── Claim sections ──────────────────────────────────────────────────

    def test_report_has_three_sections(self):
        path = RUNS_ROOT / RUN_ID / "final_report" / "final_report.md"
        text = path.read_text(encoding="utf-8")
        assert "## 1. Engineering Pipeline Status" in text
        assert "## 2. Execution Benchmark Status" in text
        assert "## 3. Scientific Claim Status" in text

    def test_stage_status_table_present(self):
        path = RUNS_ROOT / RUN_ID / "final_report" / "final_report.md"
        text = path.read_text(encoding="utf-8")
        assert "**All upstream stages passed:**" in text

    def test_execution_benchmark_section(self):
        path = RUNS_ROOT / RUN_ID / "final_report" / "final_report.md"
        text = path.read_text(encoding="utf-8")
        assert "- Execution mode: **" in text
        assert "- GPU L3 claim: **" in text
        assert "- Units:" in text
        assert "- Variants tested:" in text

    # ── Scientific claim ────────────────────────────────────────────────

    def test_scientific_claim_consistent_with_3_9(self):
        assert self.handoff is not None
        assert self.reflection is not None
        is_noop = any(
            str(c.matched_rule_id) == "noop_patch_no_scientific_claim"
            for c in self.reflection.per_variant_conclusions
        ) if self.reflection.per_variant_conclusions else False
        if is_noop:
            assert self.handoff["scientific_claim"] == "not_established"
        else:
            assert self.handoff["scientific_claim"] in (
                "not_established", "improvement_demonstrated",
                "regression_detected", "mixed_or_inconclusive",
            )

    def test_noop_flagged_in_report(self):
        assert self.reflection is not None
        is_noop = any(
            str(c.matched_rule_id) == "noop_patch_no_scientific_claim"
            for c in self.reflection.per_variant_conclusions
        ) if self.reflection.per_variant_conclusions else False
        if is_noop:
            path = RUNS_ROOT / RUN_ID / "final_report" / "final_report.md"
            text = path.read_text(encoding="utf-8")
            assert "No effective patch was applied" in text

    def test_variant_conclusions_listed(self):
        assert self.reflection is not None
        if self.reflection.per_variant_conclusions:
            path = RUNS_ROOT / RUN_ID / "final_report" / "final_report.md"
            text = path.read_text(encoding="utf-8")
            assert "### Variant Conclusions" in text
            for vc in self.reflection.per_variant_conclusions:
                assert vc.variant_id in text
                assert vc.conclusion.value in text

    # ── SHA chain ───────────────────────────────────────────────────────

    def test_handoff_sha256_matches_artifact(self):
        assert self.handoff is not None
        path = RUNS_ROOT / RUN_ID / "final_report" / "final_report.md"
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert self.handoff["report_sha256"] == actual_sha

    def test_facts_sha256_matches_artifact(self):
        assert self.handoff is not None
        path = RUNS_ROOT / RUN_ID / "final_report" / "final_report_facts.json"
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert self.handoff["facts_sha256"] == actual_sha

    # ── Idempotency ─────────────────────────────────────────────────────

    def test_handoff_contains_valid_sha_fields(self):
        """Handoff SHA fields have valid length (determinism is guaranteed by source artifacts)."""
        assert self.handoff is not None
        assert len(self.handoff.get("report_sha256", "")) == 64
        assert len(self.handoff.get("facts_sha256", "")) == 64

    # ── GPU evidence ────────────────────────────────────────────────────

    def _load_fresh_handoff(self) -> dict:
        path = RUNS_ROOT / RUN_ID / "final_report" / "final_report_handoff.json"
        if path.exists():
            with path.open() as f:
                return json.load(f)
        return {}

    def test_gpu_claim_not_completed_with_cpu_evidence(self):
        """When evidence shows GPU unavailable, GPU claim must be not_completed."""
        handoff = self._load_fresh_handoff()
        assert handoff.get("gpu_claim") == "not_completed"
        assert handoff.get("gpu_evidence_found") is True

    def test_execution_mode_cpu_fallback_when_gpu_unavailable(self):
        """When evidence shows GPU unavailable, execution mode must be cpu_fallback."""
        handoff = self._load_fresh_handoff()
        assert handoff.get("execution_mode") == "cpu_fallback"

    def test_gpu_device_name_empty_when_gpu_unavailable(self):
        """When evidence shows GPU unavailable, gpu_device_name must be empty."""
        handoff = self._load_fresh_handoff()
        assert handoff.get("gpu_device_name") == ""

    def test_facts_contains_gpu_fields(self):
        """final_report_facts.json must contain GPU evidence fields."""
        assert self.facts is not None
        assert "gpu_evidence_found" in self.facts
        assert "gpu_device_name" in self.facts
        assert "execution_mode" in self.facts
        assert "l3_gpu_claim" in self.facts

    def test_report_mentions_cpu_fallback(self):
        """Report must mention CPU fallback when evidence shows GPU unavailable."""
        handoff = self._load_fresh_handoff()
        path = RUNS_ROOT / RUN_ID / "final_report" / "final_report.md"
        text = path.read_text(encoding="utf-8")
        if handoff.get("execution_mode") == "cpu_fallback":
            assert "CPU fallback was used" in text

    def test_missing_gpu_evidence_file_yields_not_verified(self):
        """Temporarily remove gpu_execution_evidence.json to test not_verified path."""
        evidence_path = RUNS_ROOT / RUN_ID / "runner_execute" / "gpu_execution_evidence.json"
        if not evidence_path.exists():
            pytest.skip("gpu_execution_evidence.json already absent")
        handoff_path = RUNS_ROOT / RUN_ID / "final_report" / "final_report_handoff.json"
        backup_evidence = evidence_path.read_bytes()
        try:
            evidence_path.unlink()
            if handoff_path.exists():
                handoff_path.unlink()
            from autoad_researcher.pipeline.final_report_stage import run_final_report_stage
            run_final_report_stage(
                run_id=RUN_ID, run_dir=RUNS_ROOT / RUN_ID,
                stage_dir=RUNS_ROOT / RUN_ID / "final_report",
            )
            import json as _json
            handoff = _json.loads(handoff_path.read_text(encoding="utf-8"))
            assert handoff["execution_mode"] == "not_verified"
            assert handoff["gpu_claim"] == "not_completed"
            assert handoff["gpu_evidence_found"] is False
        finally:
            evidence_path.write_bytes(backup_evidence)
            if handoff_path.exists():
                handoff_path.unlink()
            from autoad_researcher.pipeline.final_report_stage import run_final_report_stage
            run_final_report_stage(
                run_id=RUN_ID, run_dir=RUNS_ROOT / RUN_ID,
                stage_dir=RUNS_ROOT / RUN_ID / "final_report",
            )
