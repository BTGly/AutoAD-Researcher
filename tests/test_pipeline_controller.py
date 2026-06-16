"""测试 PipelineController。"""

import pytest

from autoad_researcher.core import EventStore, PipelineController, PipelineResult
from autoad_researcher.harness.simple_pipeline import SimplePipelineHarness


class TestPipelineController:
    def test_run_planning_pipeline_success(self, tmp_path):
        harness = SimplePipelineHarness(runs_root=tmp_path)
        controller = PipelineController(harness=harness, runs_root=tmp_path)

        result = controller.run_planning_pipeline("run_demo")

        assert isinstance(result, PipelineResult)
        assert result.run_id == "run_demo"
        assert result.status == "success"

        assert [s.stage for s in result.stages] == [
            "experiment_planning",
            "patch_planning",
        ]
        assert [s.status for s in result.stages] == ["success", "success"]
        assert result.stages[0].artifacts == ["experiment_plan.json"]
        assert result.stages[1].artifacts == ["patch_plan.json"]

        run_dir = tmp_path / "run_demo"
        assert (run_dir / "experiment_plan.json").exists()
        assert (run_dir / "patch_plan.json").exists()

    def test_run_planning_pipeline_records_events(self, tmp_path):
        harness = SimplePipelineHarness(runs_root=tmp_path)
        controller = PipelineController(harness=harness, runs_root=tmp_path)

        controller.run_planning_pipeline("run_demo")

        events = EventStore(runs_root=tmp_path).read_events("run_demo")
        event_types = [e.event_type for e in events]

        assert event_types == [
            "run_created",
            "stage_started",
            "artifact_written",
            "stage_completed",
            "stage_started",
            "artifact_written",
            "stage_completed",
        ]

        assert events[0].payload["pipeline"] == "planning"

        assert events[1].payload["stage"] == "experiment_planning"
        assert events[2].payload["artifact"] == "experiment_plan.json"
        assert events[3].payload["stage"] == "experiment_planning"

        assert events[4].payload["stage"] == "patch_planning"
        assert events[5].payload["artifact"] == "patch_plan.json"
        assert events[6].payload["stage"] == "patch_planning"

    def test_invalid_run_id_rejected(self, tmp_path):
        harness = SimplePipelineHarness(runs_root=tmp_path)
        controller = PipelineController(harness=harness, runs_root=tmp_path)

        with pytest.raises(ValueError):
            controller.run_planning_pipeline("../escape")
