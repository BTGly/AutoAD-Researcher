"""PatchPlan — 代码修改计划 schema。"""

from pydantic import BaseModel, ConfigDict


class PatchPlan(BaseModel):
    """代码修改计划。

    所有字段必填。extra="allow" 允许 Agent 添加辅助字段
    （如 approval_notes），但不影响核心校验。
    """

    model_config = ConfigDict(extra="allow")

    target_repo: str
    files_to_inspect: list[str]
    files_to_modify: list[str]
    planned_changes: list[str]
    expected_risks: list[str]
    requires_approval: bool
