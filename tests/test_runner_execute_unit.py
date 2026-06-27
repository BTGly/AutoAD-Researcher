"""Unit tests for runner_execute_stage helper functions (no run artifacts needed)."""

import json
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from autoad_researcher.schemas.execution import ExecutionUnitPlan
from autoad_researcher.runner.models import ExperimentCommandPlan


def _make_unit(**kwargs) -> ExecutionUnitPlan:
    defaults = dict(
        unit_id="unit_test_0",
        matrix_entry_id="entry_test_0",
        variant_id=None,
        seed=42,
        workspace_id="ws_test",
        stage="full",
        command_plan_sha256="0" * 64,
        max_attempts=2,
        max_wall_time_seconds=3600,
    )
    defaults.update(kwargs)
    return ExecutionUnitPlan(**defaults)


class TestCommandPath:
    """test_command_path: verify _make_command_plan constructs correct args."""

    def test_command_path_with_default_config(self):
        from autoad_researcher.pipeline.runner_execute_stage import _make_command_plan
        unit = _make_unit()
        plan = _make_command_plan(unit, None, Path("/tmp/out"))
        assert plan.program is not None and len(plan.program) > 0
        assert "bin/run_patchcore.py" in plan.args
        assert any("outputs" in a for a in plan.args) or plan.expected_outputs

    def test_command_path_includes_seed(self):
        from autoad_researcher.pipeline.runner_execute_stage import _make_command_plan
        config = {
            "repository": {"entrypoint_path": "bin/run_patchcore.py"},
            "fixed_parameters": {"seed": 99, "backbone": "wideresnet50"},
            "dataset": {"category": "bottle"},
            "evaluation": {},
        }
        plan = _make_command_plan(_make_unit(), config, Path("/tmp/out"))
        cmd_str = " ".join(plan.args)
        assert "--seed" in cmd_str

    def test_command_path_entrypoint_from_config(self):
        from autoad_researcher.pipeline.runner_execute_stage import _make_command_plan
        config = {
            "repository": {"entrypoint_path": "bin/eval.py"},
            "fixed_parameters": {"seed": 0, "backbone": "wideresnet50"},
            "dataset": {"category": "bottle"},
            "evaluation": {},
        }
        unit = _make_unit()
        plan = _make_command_plan(unit, config, Path("/tmp/out"))
        cmd_str = " ".join(plan.args)
        assert "bin/eval.py" in cmd_str


class TestExpectedOutputs:
    """test_expected_outputs: verify expected outputs alignment."""

    def test_expected_outputs_default_non_empty(self):
        from autoad_researcher.pipeline.runner_execute_stage import _make_command_plan
        plan = _make_command_plan(_make_unit(), None, Path("/tmp/out"))
        assert len(plan.expected_outputs) > 0
        assert all(isinstance(p, str) for p in plan.expected_outputs)

    def test_expected_outputs_from_config_raw_paths(self):
        from autoad_researcher.pipeline.runner_execute_stage import _make_command_plan
        config = {
            "repository": {"entrypoint_path": "bin/run_patchcore.py"},
            "fixed_parameters": {"seed": 0},
            "dataset": {"category": "bottle"},
            "evaluation": {"raw_result_paths": ["outputs/metrics.json", "outputs/summary.csv"]},
        }
        plan = _make_command_plan(_make_unit(), config, Path("/tmp/out"))
        assert "outputs/metrics.json" in plan.expected_outputs
        assert "outputs/summary.csv" in plan.expected_outputs

    def test_expected_outputs_relative_to_results_root(self):
        from autoad_researcher.pipeline.runner_execute_stage import _make_command_plan
        results_root = Path("/tmp/test_results")
        plan = _make_command_plan(_make_unit(), None, results_root)
        assert plan.expected_outputs
        for out_path in plan.expected_outputs:
            assert not Path(out_path).is_absolute()


class TestDeterministicSha:
    """test_deterministic_sha: SHA chain determinism."""

    def test_sha256_file_deterministic(self):
        from autoad_researcher.pipeline.runner_execute_stage import _sha256_file
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("hello deterministic sha")
            tmp = Path(f.name)
        try:
            sha1 = _sha256_file(tmp)
            sha2 = _sha256_file(tmp)
            assert sha1 == sha2
            assert len(sha1) == 64
            assert all(c in "0123456789abcdef" for c in sha1)
        finally:
            tmp.unlink(missing_ok=True)

    def test_sha256_file_empty(self):
        from autoad_researcher.pipeline.runner_execute_stage import _sha256_file
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            tmp = Path(f.name)
        try:
            sha = _sha256_file(tmp)
            assert sha == hashlib.sha256(b"").hexdigest()
        finally:
            tmp.unlink(missing_ok=True)

    def test_sha256_file_large_content(self):
        from autoad_researcher.pipeline.runner_execute_stage import _sha256_file
        data = b"x" * (2 * 1024 * 1024 + 1)  # 2MB+1 (triggers chunking)
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(data)
            tmp = Path(f.name)
        try:
            sha = _sha256_file(tmp)
            assert sha == hashlib.sha256(data).hexdigest()
        finally:
            tmp.unlink(missing_ok=True)

    def test_sha256_file_gitignored(self):
        from autoad_researcher.pipeline.runner_execute_stage import _sha256_file
        with tempfile.NamedTemporaryFile(suffix=".gitkeep", delete=False) as f:
            f.write(b"")
            tmp = Path(f.name)
        try:
            sha = _sha256_file(tmp)
            assert len(sha) == 64
        finally:
            tmp.unlink(missing_ok=True)

    def test_compute_report_sha_deterministic(self):
        from autoad_researcher.pipeline.runner_execute_stage import _compute_report_sha
        from autoad_researcher.schemas.execution import RunnerIntakeReport, IntakeCheck
        report = RunnerIntakeReport(
            status="eligible",
            checks=[IntakeCheck(name="check_1", status="passed")],
            report_sha256="0" * 64,
        )
        sha1 = _compute_report_sha(report)
        sha2 = _compute_report_sha(report)
        assert sha1 == sha2
        assert len(sha1) == 64
