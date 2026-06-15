"""ExperimentPlan — 实验计划 schema。"""

from pydantic import BaseModel, ConfigDict


class ExperimentPlan(BaseModel):
    """实验计划。

    所有字段必填。extra="allow" 允许 Agent 添加辅助字段
    （如 smoke_test_mode、go_no_go_criteria），但不影响核心校验。
    """

    model_config = ConfigDict(extra="allow")

    experiment_goal: str
    baseline: str
    dataset: str
    categories: list[str]
    metrics: list[str]
    control_group: str
    experiment_group: str
    resource_budget: str
    risks: list[str]
