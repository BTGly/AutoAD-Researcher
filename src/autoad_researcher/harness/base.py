"""AgentHarness 抽象基类。

所有 harness 后端必须实现此接口。接口定义最小方法集，
确保 AutoAD Core 不依赖具体 Agent 框架。
"""

from abc import ABC, abstractmethod
from pathlib import Path

from autoad_researcher.core.run_id import run_dir_path, validate_run_id
from autoad_researcher.core.stage_result import StageResult


class AgentHarness(ABC):
    """Agent 执行内核抽象接口。

    原型控制面通过此接口调用受限 harness backend。
    """

    def __init__(self, runs_root: str | Path = "runs") -> None:
        self._runs_root = Path(runs_root)

    def _validate_run_id(self, run_id: str) -> None:
        """校验 run_id 安全性。委托给 core/run_id.py。"""
        validate_run_id(self._runs_root, run_id)

    def _run_dir(self, run_id: str) -> Path:
        """返回 runs/{run_id}/ 的绝对路径。调用前先校验 run_id。"""
        return run_dir_path(self._runs_root, run_id)

    # ------------------------------------------------------------------
    # 子类必须实现的抽象方法
    # ------------------------------------------------------------------

    @abstractmethod
    def run_experiment_planning(self, run_id: str) -> StageResult:
        """生成 experiment_plan.json。

        从 runs/{run_id}/ 读取 input_task.yaml 和 paper_summary.json，
        产出实验计划，写入 runs/{run_id}/experiment_plan.json。
        输出必须符合 ExperimentPlan schema。

        Returns:
            StageResult with stage="experiment_planning"
        """
        ...

    @abstractmethod
    def run_patch_planning(self, run_id: str) -> StageResult:
        """生成 patch_plan.json。

        从 runs/{run_id}/ 读取 input_task.yaml 和 paper_summary.json，
        产出代码修改计划，写入 runs/{run_id}/patch_plan.json。
        输出必须符合 PatchPlan schema。

        Returns:
            StageResult with stage="patch_planning"
        """
        ...
