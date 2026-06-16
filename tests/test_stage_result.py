"""测试 StageResult。"""

import pytest
from pydantic import ValidationError

from autoad_researcher.core import StageResult


class TestStageResult:
    def test_minimal_result(self):
        result = StageResult(
            run_id="run_demo",
            stage="experiment_planning",
            status="success",
            artifacts=["experiment_plan.json"],
        )

        assert result.run_id == "run_demo"
        assert result.stage == "experiment_planning"
        assert result.status == "success"
        assert result.artifacts == ["experiment_plan.json"]
        assert result.metadata == {}

    def test_rejects_unknown_status(self):
        with pytest.raises(ValidationError):
            StageResult(
                run_id="run_demo",
                stage="experiment_planning",
                status="unknown",
            )

    def test_default_artifacts_and_metadata(self):
        result = StageResult(
            run_id="run_demo",
            stage="patch_planning",
            status="skipped",
        )

        assert result.artifacts == []
        assert result.metadata == {}
