"""测试 SimplePipelineHarness 确定性输出。"""

from autoad_researcher.harness.simple_pipeline import SimplePipelineHarness
from autoad_researcher.schemas import ExperimentPlan, PatchPlan


class TestSimplePipelineHarness:
    """SimplePipelineHarness 冒烟测试。"""

    def test_experiment_plan_written_and_valid(self, tmp_path):
        harness = SimplePipelineHarness(runs_root=tmp_path)
        harness.run_experiment_planning("test_run")

        plan_path = tmp_path / "test_run" / "experiment_plan.json"
        assert plan_path.exists()

        # 输出符合 ExperimentPlan schema
        import json

        data = json.loads(plan_path.read_text())
        ExperimentPlan.model_validate(data)

    def test_patch_plan_written_and_valid(self, tmp_path):
        harness = SimplePipelineHarness(runs_root=tmp_path)
        harness.run_patch_planning("test_run")

        patch_path = tmp_path / "test_run" / "patch_plan.json"
        assert patch_path.exists()

        import json

        data = json.loads(patch_path.read_text())
        PatchPlan.model_validate(data)
