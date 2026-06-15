"""AgentHarness 抽象基类。

所有 harness 后端必须实现此接口。接口定义最小方法集，
确保 AutoAD Core 不依赖具体 Agent 框架。
"""

from abc import ABC, abstractmethod


class AgentHarness(ABC):
    """Agent 执行内核抽象接口。

    AutoAD Core 通过此接口调用 harness backend，
    不感知具体是 SimplePipelineHarness 还是 DeepAgentsHarness。
    """

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
