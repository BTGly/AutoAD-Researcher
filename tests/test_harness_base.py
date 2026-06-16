"""测试 AgentHarness 基类的 run_id 安全校验。"""

import pytest

from autoad_researcher.harness.simple_pipeline import SimplePipelineHarness


class TestRunIdValidation:
    """run_id 校验 — 安全边界测试。"""

    @pytest.mark.parametrize(
        "bad_run_id",
        [
            "",
            ".",
            "..",
            "...",
            "....",
            "../escape",
            "foo/bar",
            "foo\\bar",
        ],
    )
    def test_invalid_run_id_rejected(self, tmp_path, bad_run_id):
        harness = SimplePipelineHarness(runs_root=tmp_path)
        with pytest.raises(ValueError):
            harness.run_experiment_planning(bad_run_id)

    @pytest.mark.parametrize(
        "valid_run_id",
        [
            "run_demo",
            "run-001",
            "run_001",
            "run.v1",
            "exp_20260615",
        ],
    )
    def test_valid_run_id_accepted(self, tmp_path, valid_run_id):
        harness = SimplePipelineHarness(runs_root=tmp_path)

        exp_result = harness.run_experiment_planning(valid_run_id)
        patch_result = harness.run_patch_planning(valid_run_id)

        assert exp_result.status == "success"
        assert patch_result.status == "success"

        run_dir = tmp_path / valid_run_id
        assert (run_dir / "experiment_plan.json").exists()
        assert (run_dir / "patch_plan.json").exists()
