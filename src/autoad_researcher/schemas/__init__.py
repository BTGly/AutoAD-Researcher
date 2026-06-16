"""AutoAD schema 定义。

所有阶段产物的 Pydantic 数据模型集中于此。
harness、pipeline、storage 等模块不应在各自目录内重复定义 schema。
"""

from autoad_researcher.schemas.clarification import (
    ArtifactReference,
    ClarificationCategory,
    ClarificationContext,
    ClarificationQuestion,
    ClarificationStatus,
    ClarifiedTask,
    KnownFact,
    MissingInformation,
    QuestionAnswerType,
)
from autoad_researcher.schemas.experiment import ExperimentPlan
from autoad_researcher.schemas.ideas import (
    IdeaCandidate,
    IdeaContext,
    IdeaGenerationResult,
    IdeaMode,
    IdeaRouteDecision,
)
from autoad_researcher.schemas.intake import (
    InputTask,
    SourceEntry,
    SourceKind,
    SourceManifest,
)
from autoad_researcher.schemas.patch import PatchPlan
from autoad_researcher.schemas.readers import (
    EvidenceReference,
    KnowledgeState,
    PaperSummary,
    RepositorySummary,
)

__all__ = [
    "ArtifactReference",
    "ClarificationCategory",
    "ClarificationContext",
    "ClarificationQuestion",
    "ClarificationStatus",
    "ClarifiedTask",
    "EvidenceReference",
    "ExperimentPlan",
    "IdeaCandidate",
    "IdeaContext",
    "IdeaGenerationResult",
    "IdeaMode",
    "IdeaRouteDecision",
    "InputTask",
    "KnownFact",
    "KnowledgeState",
    "MissingInformation",
    "PaperSummary",
    "PatchPlan",
    "QuestionAnswerType",
    "RepositorySummary",
    "SourceEntry",
    "SourceKind",
    "SourceManifest",
]
