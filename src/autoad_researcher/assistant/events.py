"""AssistantEvent envelope — 粗粒度事件类型，不是用户行为枚举。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AssistantEventType = Literal[
    "user_input",
    "source_input",
    "artifact_update",
    "task_decision",
    "system_update",
    "progress_query",
    "unknown",
]

# 用于 IntentRouter 标注的辅助标签，帮助 TransitionPolicy 理解用户意图
RouterLabel = Literal[
    "correction",
    "goal_update",
    "budget_constraint",
    "material_upload",
    "confirmation",
    "rejection",
    "revision_request",
    "status_inquiry",
    "next_step_request",
    "clarification",
]


class AssistantEvent(BaseModel):
    """Assistant 感知的最小输入 event envelope。

    不枚举所有用户行为。具体内容放在 payload 和 router_labels 中。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(min_length=1)
    event_type: AssistantEventType

    # 用户原文 / 系统消息
    payload: dict[str, Any] = Field(default_factory=dict)

    # IntentRouter 输出的辅助分类标签
    router_labels: list[RouterLabel] = Field(default_factory=list)

    # 分类置信度 0..1
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    # 元数据
    source: Literal["user", "system"] = "user"
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
