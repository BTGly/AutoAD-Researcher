"""AgentHarness 抽象基类。

所有 harness 后端必须实现此接口。接口定义最小方法集，
确保 AutoAD Core 不依赖具体 Agent 框架。
"""

import re
from abc import ABC, abstractmethod
from pathlib import Path


# 只允许字母、数字、下划线、连字符、点号
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class AgentHarness(ABC):
    """Agent 执行内核抽象接口。

    AutoAD Core 通过此接口调用 harness backend，
    不感知具体是 SimplePipelineHarness 还是 DeepAgentsHarness。
    """

    def __init__(self, runs_root: str | Path = "runs") -> None:
        self._runs_root = Path(runs_root)

    def _validate_run_id(self, run_id: str) -> None:
        """校验 run_id 安全性。

        拒绝路径穿越字符（/, \\, ..），只允许 [A-Za-z0-9_.-]+。
        同时验证最终路径不逃逸 runs_root。
        """
        if not run_id:
            raise ValueError("run_id must not be empty")
        if run_id == ".." or ".." in run_id.split("/") or ".." in run_id.split("\\"):
            raise ValueError(f"run_id contains path traversal: {run_id!r}")
        if not _RUN_ID_PATTERN.match(run_id):
            raise ValueError(
                f"run_id must match {_RUN_ID_PATTERN.pattern}, got: {run_id!r}"
            )

        resolved_root = self._runs_root.resolve()
        resolved_run = (self._runs_root / run_id).resolve()

        try:
            resolved_run.relative_to(resolved_root)
        except ValueError:
            raise ValueError(
                f"run_id escapes runs_root: "
                f"runs_root={resolved_root}, resolved={resolved_run}"
            ) from None

    def _run_dir(self, run_id: str) -> Path:
        """返回 runs/{run_id}/ 的绝对路径。调用前先校验 run_id。"""
        self._validate_run_id(run_id)
        return self._runs_root / run_id

    # ------------------------------------------------------------------
    # 子类必须实现的抽象方法
    # ------------------------------------------------------------------

    @abstractmethod
    def run_experiment_planning(self, run_id: str) -> None:
        """生成 experiment_plan.json。

        从 runs/{run_id}/ 读取 input_task.yaml 和 paper_summary.json，
        产出实验计划，写入 runs/{run_id}/experiment_plan.json。
        输出必须符合 ExperimentPlan schema。
        """
        ...

    @abstractmethod
    def run_patch_planning(self, run_id: str) -> None:
        """生成 patch_plan.json。

        从 runs/{run_id}/ 读取 input_task.yaml 和 paper_summary.json，
        产出代码修改计划，写入 runs/{run_id}/patch_plan.json。
        输出必须符合 PatchPlan schema。
        """
        ...
