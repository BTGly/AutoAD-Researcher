"""ResearchTaskDraftV1 — 五要素研究任务草案 schema."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchTaskDraftV1(BaseModel):
    """五要素研究任务草案。

    只定义研究目标和评价约束，不决定具体方法实现。
    禁止包含 method / algorithm / hyperparameters / patch plan / variant choice。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)

    # ── 五要素 ──
    metric_command: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    metric_direction: Literal["maximize", "minimize"]

    baseline: str = Field(min_length=1)
    baseline_value: float | None = None

    ambition: Literal["push_max", "reach_target", "beat_baseline"]
    ambition_target: float | None = None

    scope: Literal["novelty_leaning", "effect_leaning", "mixed"]

    constraints: list[str] = Field(default_factory=list)

    # ── 复用 AutoAD Intake 语义 ──
    dataset: str | None = None
    compute_budget: str | None = None
    user_idea: str | None = None

    # ── 证据追溯 ──
    evidence_ids: list[str] = Field(default_factory=list)

    # ── 状态 ──
    confirmation: Literal["draft", "confirmed", "rejected", "revised"] = "draft"
    confirmed_by_user_at: datetime | None = None
    confirmation_evidence_id: str | None = None
