"""Intent Clarifier schemas — 已澄清任务、缺口和问题。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.schemas.intake import InputTask
from autoad_researcher.schemas.readers import PaperSummary, RepositorySummary


# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------

ClarificationStatus = Literal[
    "ready",
    "has_nonblocking_questions",
    "needs_blocking_input",
]

ClarificationCategory = Literal[
    "task_scope",
    "domain",
    "method",
    "baseline",
    "dataset",
    "metrics",
    "resources",
    "repository",
    "scientific_validity",
    "other",
]

QuestionAnswerType = Literal[
    "free_text",
    "single_choice",
    "multiple_choice",
    "boolean",
]


# ------------------------------------------------------------------
# ArtifactReference
# ------------------------------------------------------------------


class ArtifactReference(BaseModel):
    """一个已落盘事实的位置。"""

    model_config = ConfigDict(extra="forbid")

    artifact: Literal[
        "input_task.yaml",
        "paper_summary.json",
        "repo_summary.json",
    ]
    locator: str
    source_id: str | None = None


# ------------------------------------------------------------------
# KnownFact
# ------------------------------------------------------------------


class KnownFact(BaseModel):
    """Clarifier 已确认的事实。"""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    category: ClarificationCategory
    statement: str
    references: list[ArtifactReference] = Field(min_length=1)


# ------------------------------------------------------------------
# MissingInformation
# ------------------------------------------------------------------


class MissingInformation(BaseModel):
    """仍然缺失、可能影响后续决策的信息。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    category: ClarificationCategory
    field: str
    reason: str
    blocking: bool = False

    suggested_values: list[str] = Field(default_factory=list)
    references: list[ArtifactReference] = Field(default_factory=list)


# ------------------------------------------------------------------
# ClarificationQuestion
# ------------------------------------------------------------------


class ClarificationQuestion(BaseModel):
    """向用户提出的一项关键问题。"""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    missing_item_id: str

    question: str
    why_needed: str

    answer_type: QuestionAnswerType
    options: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_options(self):
        if self.answer_type in {"single_choice", "multiple_choice"} and not self.options:
            raise ValueError("choice question must provide options")
        return self


# ------------------------------------------------------------------
# ClarificationContext (backend input, not persisted)
# ------------------------------------------------------------------


class ClarificationContext(BaseModel):
    """Intent Clarifier backend 的结构化输入。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task: InputTask

    paper_summary: PaperSummary | None = None
    repo_summary: RepositorySummary | None = None


# ------------------------------------------------------------------
# ClarifiedTask
# ------------------------------------------------------------------


class ClarifiedTask(BaseModel):
    """当前已知任务、缺口和待确认问题。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: ClarificationStatus

    original_request: str
    source_ids: list[str] = Field(default_factory=list)

    target_domain: str | None = None
    user_idea: str | None = None
    baseline: str | None = None
    dataset: str | None = None
    metrics: list[str] = Field(default_factory=list)
    compute_budget: str | None = None
    constraints: list[str] = Field(default_factory=list)

    known_facts: list[KnownFact] = Field(default_factory=list)
    missing_information: list[MissingInformation] = Field(default_factory=list)
    questions: list[ClarificationQuestion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_consistency(self):
        fact_ids = [f.fact_id for f in self.known_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("duplicate fact_id")

        missing_ids = [m.item_id for m in self.missing_information]
        if len(missing_ids) != len(set(missing_ids)):
            raise ValueError("duplicate missing information item_id")

        question_ids = [q.question_id for q in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("duplicate question_id")

        known_missing = set(missing_ids)
        for q in self.questions:
            if q.missing_item_id not in known_missing:
                raise ValueError("question references unknown missing item")

        has_blocking = any(m.blocking for m in self.missing_information)
        if has_blocking:
            expected = "needs_blocking_input"
        elif self.questions:
            expected = "has_nonblocking_questions"
        else:
            expected = "ready"

        if self.status != expected:
            raise ValueError(f"clarification status must be {expected!r}")

        return self
