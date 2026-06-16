"""测试 PipelineController。"""

from pathlib import Path

import pytest

from autoad_researcher.core import EventStore, PipelineController, PipelineResult, StageResult
from autoad_researcher.harness.base import AgentHarness
from autoad_researcher.harness.simple_pipeline import SimplePipelineHarness


# ------------------------------------------------------------------
# Fake harnesses for failure path testing
# ------------------------------------------------------------------


class FailingExperimentHarness(AgentHarness):
    """experiment_planning 直接抛异常。"""

    def run_experiment_planning(self, run_id: str) -> StageResult:
        raise RuntimeError("experiment boom")

    def run_patch_planning(self, run_id: str) -> StageResult:
        raise AssertionError("patch stage should not run")


class FailingPatchHarness(AgentHarness):
    """experiment_planning 成功，patch_planning 抛异常。"""

    def run_experiment_planning(self, run_id: str) -> StageResult:
        return StageResult(
            run_id=run_id,
            stage="experiment_planning",
            status="success",
            artifacts=["experiment_plan.json"],
            metadata={"backend": "fake"},
        )

    def run_patch_planning(self, run_id: str) -> StageResult:
        raise RuntimeError("patch boom")


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


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


class TestPipelineControllerFailure:
    """失败路径测试。"""

    def test_experiment_stage_failure_returns_failed_result(self, tmp_path):
        harness = FailingExperimentHarness(runs_root=tmp_path)
        controller = PipelineController(harness=harness, runs_root=tmp_path)

        result = controller.run_planning_pipeline("run_demo")

        assert result.run_id == "run_demo"
        assert result.status == "failed"
        assert result.stages == []
        assert result.failed_stage == "experiment_planning"
        assert result.error_type == "RuntimeError"
        assert result.error_message == "experiment boom"

        events = EventStore(runs_root=tmp_path).read_events("run_demo")
        assert [e.event_type for e in events] == [
            "run_created",
            "stage_failed",
        ]
        assert events[1].payload["stage"] == "experiment_planning"
        assert events[1].payload["error_type"] == "RuntimeError"

    def test_patch_stage_failure_preserves_completed_stages(self, tmp_path):
        harness = FailingPatchHarness(runs_root=tmp_path)
        controller = PipelineController(harness=harness, runs_root=tmp_path)

        result = controller.run_planning_pipeline("run_demo")

        assert result.status == "failed"
        assert result.failed_stage == "patch_planning"
        assert result.error_type == "RuntimeError"
        assert result.error_message == "patch boom"

        assert len(result.stages) == 1
        assert result.stages[0].stage == "experiment_planning"
        assert result.stages[0].status == "success"

        events = EventStore(runs_root=tmp_path).read_events("run_demo")
        assert [e.event_type for e in events] == [
            "run_created",
            "stage_failed",
        ]
        assert events[1].payload["stage"] == "patch_planning"
