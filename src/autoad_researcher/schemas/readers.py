"""Reader schemas — 论文摘要与代码仓库摘要的正式数据模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ------------------------------------------------------------------
# EvidenceReference
# ------------------------------------------------------------------


class EvidenceReference(BaseModel):
    """Reader 输出所依据的材料位置。"""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    locator: str
    description: str | None = None


# ------------------------------------------------------------------
# KnowledgeState
# ------------------------------------------------------------------

KnowledgeState = Literal["yes", "no", "unknown"]


# ------------------------------------------------------------------
# PaperSummary
# ------------------------------------------------------------------


class PaperSummary(BaseModel):
    """面向后续方法迁移的论文结构化摘要。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    source_id: str

    title: str | None = None
    research_problem: str
    core_idea: str

    method_components: list[str] = Field(default_factory=list)
    data_assumptions: list[str] = Field(default_factory=list)
    training_objectives: list[str] = Field(default_factory=list)

    requires_anomaly_labels: KnowledgeState = "unknown"
    datasets: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    code_available: KnowledgeState = "unknown"

    potential_transfer_points: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)


# ------------------------------------------------------------------
# RepositorySummary
# ------------------------------------------------------------------


class RepositorySummary(BaseModel):
    """面向实验修改和执行的仓库结构化摘要。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    source_id: str

    repository_name: str | None = None
    primary_language: str | None = None

    important_paths: list[str] = Field(default_factory=list)
    training_entrypoints: list[str] = Field(default_factory=list)
    inference_entrypoints: list[str] = Field(default_factory=list)
    evaluation_entrypoints: list[str] = Field(default_factory=list)
    configuration_paths: list[str] = Field(default_factory=list)

    baseline_methods: list[str] = Field(default_factory=list)

    editable_paths: list[str] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)

    test_commands: list[str] = Field(default_factory=list)
    evaluation_script_fingerprint: str | None = None

    unresolved_questions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
