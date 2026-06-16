"""StageResult — harness stage 执行结果。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


StageStatus = Literal["success", "failed", "skipped", "not_implemented"]


class StageResult(BaseModel):
    """单个 harness stage 的结构化执行结果。

    仅包含可以被 JSON 序列化的字段。
    artifacts 存文件名而非绝对路径，路径解析由 ArtifactStore 完成。
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    stage: str
    status: StageStatus
    artifacts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
