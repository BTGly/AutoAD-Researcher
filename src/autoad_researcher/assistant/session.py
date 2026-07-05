"""AutoADAssistantSession — 最小控制状态，不是对话大表单。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AssistantMode = Literal[
    "goal_alignment",
    "material_alignment",
    "artifact_processing",
    "intent_structuring",
    "task_confirmation",
    "pipeline_ready",
    "progress_reporting",
]


class SourceState(BaseModel):
    """材料处理进度汇总。细节不在 session 中，在 artifact 中。"""

    model_config = ConfigDict(extra="forbid")

    source_ids: list[str] = Field(default_factory=list)
    registered_ids: list[str] = Field(default_factory=list)
    parsed_ids: list[str] = Field(default_factory=list)
    failed_ids: list[str] = Field(default_factory=list)


class TaskControlState(BaseModel):
    """任务确认的控制状态。detail 在 artifact 中。"""

    model_config = ConfigDict(extra="forbid")

    draft_ref: str | None = None
    confirmed_ref: str | None = None
    has_blocking_gaps: bool = True
    ready_for_pipeline: bool = False
    execution_approved: bool = False


class InteractionState(BaseModel):
    """用户交互的轻量摘要。"""

    model_config = ConfigDict(extra="forbid")

    summary_ref: str | None = None
    last_user_correction_ref: str | None = None
    pending_user_decision: str | None = None


class AutoADAssistantSession(BaseModel):
    """Assistant 会话的最小控制状态。

    - mode：当前工作模式（goal_alignment / intent_structuring / ...）
    - sources：材料总体处理到哪里
    - task：任务草案是否存在、是否确认、是否允许进入 pipeline
    - interaction：最近交互摘要

    用户原话、候选参数、确认参数、缺失项主要放在 artifact 中。
    ready_for_pipeline ≠ execution_approved：草案确认只表示任务边界已定，不批准执行。
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    mode: AssistantMode = "goal_alignment"

    sources: SourceState = Field(default_factory=SourceState)
    task: TaskControlState = Field(default_factory=TaskControlState)
    interaction: InteractionState = Field(default_factory=InteractionState)

    what_we_know_ref: str | None = None

    last_event_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
