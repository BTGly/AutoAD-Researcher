"""Input intake schemas — 用户原始请求与输入材料索引。

InputTask：用户任务和已知约束（允许不完整，留给 Intent Clarifier 补充）。
SourceEntry / SourceManifest：用户提供材料的结构化索引。
"""

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ------------------------------------------------------------------
# SourceKind
# ------------------------------------------------------------------

SourceKind = Literal[
    "paper_pdf",
    "paper_text",
    "repository",
    "dataset",
    "baseline_config",
    "method_idea",
    "experiment_history",
    "other",
]


# source_id 安全标识：[A-Za-z0-9][A-Za-z0-9._-]{0,63}
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# ------------------------------------------------------------------
# SourceEntry
# ------------------------------------------------------------------


class SourceEntry(BaseModel):
    """用户提供或系统登记的一项输入材料。"""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    kind: SourceKind

    # 用户原始提供的位置：URL、路径或逻辑标识
    original_reference: str

    # 如果材料已复制进 run workspace，记录相对 run_dir 的路径
    stored_path: str | None = None

    media_type: str | None = None
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)

    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_source_id_format(self):
        if not _SOURCE_ID_RE.match(self.source_id):
            raise ValueError(
                f"source_id must match {_SOURCE_ID_RE.pattern}, "
                f"got: {self.source_id!r}"
            )
        return self


# ------------------------------------------------------------------
# SourceManifest
# ------------------------------------------------------------------


class SourceManifest(BaseModel):
    """一个 run 的输入材料清单。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: datetime
    sources: list[SourceEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_source_ids(self):
        ids = [s.source_id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source_id in source manifest")
        return self


# ------------------------------------------------------------------
# InputTask
# ------------------------------------------------------------------


class InputTask(BaseModel):
    """用户原始任务和已经明确的约束。

    允许关键字段暂时为空 — 后续由 Intent Clarifier 补充。
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str

    # 原始用户请求，不应被摘要替换
    request: str = Field(min_length=1)

    # 对应 SourceManifest 中的 source_id
    source_ids: list[str] = Field(default_factory=list)

    target_domain: str | None = None
    user_idea: str | None = None
    baseline: str | None = None
    dataset: str | None = None
    compute_budget: str | None = None
    primary_metrics: list[str] = Field(default_factory=list)

    constraints: list[str] = Field(default_factory=list)

    @field_validator("baseline", "dataset", "compute_budget", "user_idea", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        return value
