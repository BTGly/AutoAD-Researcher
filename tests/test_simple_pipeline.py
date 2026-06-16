"""测试 SimplePipelineHarness 确定性输出。"""

import json

from autoad_researcher.core import EventStore
from autoad_researcher.harness.simple_pipeline import SimplePipelineHarness
from autoad_researcher.schemas import ExperimentPlan, PatchPlan


class TestSimplePipelineHarness:
    """SimplePipelineHarness 冒烟测试。"""

    def test_experiment_plan_written_and_valid(self, tmp_path):
        harness = SimplePipelineHarness(runs_root=tmp_path)
        result = harness.run_experiment_planning("test_run")

        assert result.run_id == "test_run"
        assert result.stage == "experiment_planning"
        assert result.status == "success"
        assert result.artifacts == ["experiment_plan.json"]
        assert result.metadata["backend"] == "simple_pipeline"

        plan_path = tmp_path / "test_run" / "experiment_plan.json"
        assert plan_path.exists()

        data = json.loads(plan_path.read_text())
        ExperimentPlan.model_validate(data)

    def test_patch_plan_written_and_valid(self, tmp_path):
        harness = SimplePipelineHarness(runs_root=tmp_path)
        result = harness.run_patch_planning("test_run")

        assert result.run_id == "test_run"
        assert result.stage == "patch_planning"
        assert result.status == "success"
        assert result.artifacts == ["patch_plan.json"]
        assert result.metadata["backend"] == "simple_pipeline"

        patch_path = tmp_path / "test_run" / "patch_plan.json"
        assert patch_path.exists()

        data = json.loads(patch_path.read_text())
        PatchPlan.model_validate(data)

    def test_simple_pipeline_records_artifact_events(self, tmp_path):
        harness = SimplePipelineHarness(runs_root=tmp_path)
        harness.run_experiment_planning("test_run")
        harness.run_patch_planning("test_run")

        events = EventStore(runs_root=tmp_path).read_events("test_run")
        event_types = [e.event_type for e in events]

        assert event_types == [
            "artifact_written",
            "artifact_written",
        ]

        artifacts = [e.payload["artifact"] for e in events]
        assert artifacts == [
            "experiment_plan.json",
            "patch_plan.json",
        ]
