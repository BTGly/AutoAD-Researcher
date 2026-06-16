"""Idea schemas — 候选科研方案的统一数据协议。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.schemas.clarification import (
    ArtifactReference,
    ClarifiedTask,
)
from autoad_researcher.schemas.readers import PaperSummary, RepositorySummary


# ------------------------------------------------------------------
# IdeaMode
# ------------------------------------------------------------------

IdeaMode = Literal[
    "direct_user_idea",
    "idea_decomposition",
    "multi_agent_exploration",
]


# ------------------------------------------------------------------
# IdeaRouteDecision
# ------------------------------------------------------------------


class IdeaRouteDecision(BaseModel):
    """Idea Source Router 的确定性决策。"""

    model_config = ConfigDict(extra="forbid")

    mode: IdeaMode
    requested_mode: IdeaMode | None = None
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_mode_consistency(self):
        if self.requested_mode is not None and self.mode != self.requested_mode:
            raise ValueError("mode must match requested_mode when both are set")
        return self


# ------------------------------------------------------------------
# IdeaContext
# ------------------------------------------------------------------


class IdeaContext(BaseModel):
    """Idea backend 的结构化输入快照。

    采用完整快照而非只传路径，保证：
    - backend 不需要自己读取文件
    - 生成时可完整审计上下文
    - 原始 artifact 更新后仍知道当时用了什么
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    route: IdeaRouteDecision

    clarified_task: ClarifiedTask
    paper_summary: PaperSummary | None = None
    repo_summary: RepositorySummary | None = None


# ------------------------------------------------------------------
# IdeaCandidate
# ------------------------------------------------------------------


class IdeaCandidate(BaseModel):
    """一个可验证的候选科研方案。"""

    model_config = ConfigDict(extra="forbid")

    idea_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)

    insertion_point: str = Field(min_length=1)
    rationale: str = Field(min_length=1)

    expected_benefits: list[str] = Field(default_factory=list)
    implementation_risks: list[str] = Field(default_factory=list)
    scientific_risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    minimum_experiment: str = Field(min_length=1)

    estimated_cost: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)

    evidence: list[ArtifactReference] = Field(min_length=1)


# ------------------------------------------------------------------
# IdeaGenerationResult
# ------------------------------------------------------------------


class IdeaGenerationResult(BaseModel):
    """所有 Idea backend 的统一输出。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    mode: IdeaMode

    candidates: list[IdeaCandidate] = Field(min_length=1, max_length=3)

    disagreements: list[str] = Field(default_factory=list)
    recommended_candidate_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_candidate_relations(self):
        cids = [c.idea_id for c in self.candidates]
        if len(cids) != len(set(cids)):
            raise ValueError("duplicate idea_id in candidates")

        if len(self.recommended_candidate_ids) != len(set(self.recommended_candidate_ids)):
            raise ValueError("duplicate recommended candidate id")

        unknown = set(self.recommended_candidate_ids) - set(cids)
        if unknown:
            raise ValueError("recommended candidate id not found in candidates")

        if self.mode == "direct_user_idea" and len(self.candidates) != 1:
            raise ValueError("direct_user_idea must produce exactly one candidate")

        return self
