"""PipelineController — minimal AutoAD pipeline orchestration.

PipelineController 负责 run 级生命周期：
- run_created / stage_failed 事件
- stage 调用顺序
- PipelineResult 聚合（含失败字段）

Harness 负责 stage 级生命周期。
ArtifactStore 负责 artifact 级生命周期。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.core.events import EventStore
from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.core.stage_result import StageResult
from autoad_researcher.harness.base import AgentHarness


PipelineStatus = Literal["success", "failed"]


class PipelineResult(BaseModel):
    """一次 pipeline 执行的结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: PipelineStatus
    stages: list[StageResult] = Field(default_factory=list)

    # 失败时填充
    failed_stage: str | None = None
    error_type: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def _check_failure_fields_consistency(self):
        if self.status == "success":
            if self.failed_stage is not None:
                raise ValueError("success result must not set failed_stage")
            if self.error_type is not None:
                raise ValueError("success result must not set error_type")
        elif self.status == "failed":
            if self.failed_stage is None:
                raise ValueError("failed result must set failed_stage")
            if self.error_type is None:
                raise ValueError("failed result must set error_type")
        return self


class PipelineController:
    """最小 AutoAD pipeline 编排器。

    当前只串联：
    1. experiment_planning
    2. patch_planning
    """

    def __init__(
        self,
        harness: AgentHarness,
        runs_root: str = "runs",
    ) -> None:
        self._harness = harness
        self._runs_root = runs_root
        self._events = EventStore(runs_root=runs_root)

    def run_planning_pipeline(self, run_id: str) -> PipelineResult:
        """运行最小规划 pipeline。

        步骤：
        1. 校验 run_id，创建 run_dir
        2. 写 run_created 事件
        3. 依次执行 experiment_planning → patch_planning
        4. 任一 stage 失败则记录 stage_failed 并返回 failed result
        5. 收集 StageResult，返回 PipelineResult
        """
        run_dir = run_dir_path(self._runs_root, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        self._events.record_run_created(
            run_id,
            payload={"pipeline": "planning"},
        )

        stages: list[StageResult] = []

        try:
            experiment_result = self._harness.run_experiment_planning(run_id)
            stages.append(experiment_result)
        except Exception as exc:
            return self._handle_stage_failure(
                run_id=run_id,
                stage="experiment_planning",
                stages=stages,
                exc=exc,
            )

        try:
            patch_result = self._harness.run_patch_planning(run_id)
            stages.append(patch_result)
        except Exception as exc:
            return self._handle_stage_failure(
                run_id=run_id,
                stage="patch_planning",
                stages=stages,
                exc=exc,
            )

        return PipelineResult(
            run_id=run_id,
            status="success",
            stages=stages,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _handle_stage_failure(
        self,
        *,
        run_id: str,
        stage: str,
        stages: list[StageResult],
        exc: Exception,
    ) -> PipelineResult:
        error_type = type(exc).__name__
        error_message = str(exc)

        self._events.record_stage_failed(
            run_id,
            stage,
            backend=self._backend_name(),
            error_type=error_type,
            error_message=error_message,
        )

        return PipelineResult(
            run_id=run_id,
            status="failed",
            stages=stages,
            failed_stage=stage,
            error_type=error_type,
            error_message=error_message,
        )

    def _backend_name(self) -> str:
        return self._harness.__class__.__name__
