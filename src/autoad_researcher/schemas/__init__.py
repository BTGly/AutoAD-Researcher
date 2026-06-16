"""AutoAD schema 定义。

所有阶段产物的 Pydantic 数据模型集中于此。
harness、pipeline、storage 等模块不应在各自目录内重复定义 schema。
"""

from autoad_researcher.schemas.decisions import (
    ConfirmedDecision,
    DecisionCandidate,
    DecisionCandidateSource,
    DecisionConfirmationSource,
    DecisionEvidence,
)
from autoad_researcher.schemas.benchmark import (
    BenchmarkDataset,
    BenchmarkEvaluationContract,
    BenchmarkMetric,
    BenchmarkReproducibility,
    BenchmarkRepository,
    BenchmarkSafety,
    InternalBenchmarkCase,
)
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
    EstimatedIdeaCost,
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
    "BenchmarkDataset",
    "BenchmarkEvaluationContract",
    "BenchmarkMetric",
    "BenchmarkReproducibility",
    "BenchmarkRepository",
    "BenchmarkSafety",
    "ClarificationCategory",
    "ClarificationContext",
    "ConfirmedDecision",
    "DecisionCandidate",
    "DecisionCandidateSource",
    "DecisionConfirmationSource",
    "DecisionEvidence",
    "ClarificationQuestion",
    "ClarificationStatus",
    "ClarifiedTask",
    "EstimatedIdeaCost",
    "EvidenceReference",
    "ExperimentPlan",
    "IdeaCandidate",
    "IdeaContext",
    "IdeaGenerationResult",
    "IdeaMode",
    "IdeaRouteDecision",
    "InputTask",
    "InternalBenchmarkCase",
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

# Resolve forward references: ClarifiedTask → DecisionCandidate, ConfirmedDecision
ClarifiedTask.model_rebuild()
