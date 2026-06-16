"""Decision provenance schemas — 关键参数的候选来源与确认来源协议。

候选来源和确认来源必须作为不同类型分离：
- 候选（DecisionCandidate）来自 repo、论文、历史或系统推荐
- 确认（ConfirmedDecision）来自用户直接提供或用户确认候选
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

# source → expected artifact for consistency validation
_EXPECTED_ARTIFACTS: dict[str, set[str]] = {
    "repo_detected": {"repo_summary.json"},
    "paper_mentioned": {"paper_summary.json"},
}


# ------------------------------------------------------------------
# DecisionEvidence
# ------------------------------------------------------------------


class DecisionEvidence(BaseModel):
    """一条候选值的来源证据。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: DecisionCandidateSource
    rationale: str = Field(min_length=1)
    references: list[ArtifactReference] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_references_match_source(self):
        expected = _EXPECTED_ARTIFACTS.get(self.source)
        if expected is not None:
            ref_artifacts = {r.artifact for r in self.references}
            if not ref_artifacts & expected:
                raise ValueError(
                    f"{self.source} evidence must reference "
                    f"at least one of {expected}, got {ref_artifacts}"
                )
        return self


# ------------------------------------------------------------------
# DecisionCandidate
# ------------------------------------------------------------------


class DecisionCandidate(BaseModel):
    """一个待用户确认的候选值。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: str = Field(min_length=1)
    evidence: list[DecisionEvidence] = Field(min_length=1)


# ------------------------------------------------------------------
# ConfirmedDecision
# ------------------------------------------------------------------


class ConfirmedDecision(BaseModel):
    """已经确认的正式参数值。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: str = Field(min_length=1)
    source: DecisionConfirmationSource
    evidence: str = Field(min_length=1)
