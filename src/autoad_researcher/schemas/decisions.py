"""Decision provenance schemas — 关键参数的候选来源与确认来源协议。

候选来源和确认来源必须作为不同类型分离：
- 候选（DecisionCandidate）来自 repo、论文、历史或系统推荐
- 确认（ConfirmedDecision）来自用户直接提供或用户确认候选
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.schemas.clarification import ArtifactReference


# ------------------------------------------------------------------
# Source types
# ------------------------------------------------------------------

DecisionCandidateSource = Literal[
    "repo_detected",
    "paper_mentioned",
    "history_detected",
    "system_recommended",
]

DecisionConfirmationSource = Literal[
    "user_provided",
    "user_confirmed",
]


# ------------------------------------------------------------------
# DecisionEvidence
# ------------------------------------------------------------------


class DecisionEvidence(BaseModel):
    """一条候选值的来源证据。"""

    model_config = ConfigDict(extra="forbid")

    source: DecisionCandidateSource
    rationale: str = Field(min_length=1)
    references: list[ArtifactReference] = Field(min_length=1)


# ------------------------------------------------------------------
# DecisionCandidate
# ------------------------------------------------------------------


class DecisionCandidate(BaseModel):
    """一个待用户确认的候选值。"""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    evidence: list[DecisionEvidence] = Field(min_length=1)


# ------------------------------------------------------------------
# ConfirmedDecision
# ------------------------------------------------------------------


class ConfirmedDecision(BaseModel):
    """已经确认的正式参数值。"""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    source: DecisionConfirmationSource
    evidence: str = Field(min_length=1)
