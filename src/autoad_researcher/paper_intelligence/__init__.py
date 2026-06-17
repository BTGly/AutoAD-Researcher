"""Step 3.2 Paper Intelligence Capability contracts."""

from autoad_researcher.paper_intelligence.attestation import (
    DEFAULT_MAX_SOURCE_BYTES,
    SOURCE_FAILURE_CODES,
    attest_paper_source,
    compute_pdf_sha256,
    estimate_page_count,
)
from autoad_researcher.paper_intelligence.control_models import (
    AnalysisProgress,
    PaperAnalysisControlSignal,
    PaperIntelligenceStatus,
)
from autoad_researcher.paper_intelligence.errors import (
    PaperBudgetError,
    PaperEvidenceError,
    PaperIntelligenceContractError,
    PaperParseError,
    PaperRepairExhaustedError,
    PaperSourceError,
    PaperValidationError,
)
from autoad_researcher.paper_intelligence.evidence_models import (
    EvidenceIndexRecord,
    PaperEvidenceRef,
    PaperFigureEvidenceRef,
    PaperReferenceEvidenceRef,
    PaperTableEvidenceRef,
    PaperTextEvidenceRef,
    WebPaperEvidenceRef,
)
from autoad_researcher.paper_intelligence.mineru_provider import (
    MINERU_PIPELINE_V1_PROFILE,
    FixtureMinerUProvider,
    MinerUProfileConfig,
    MinerUProvider,
)
from autoad_researcher.paper_intelligence.models import (
    MethodComponent,
    PaperAgentBudget,
    PaperClaim,
    PaperIdeaSourceCandidate,
    PaperIntelligenceRequest,
    PaperMentionedCandidate,
    PaperReaderResult,
    PaperSource,
    PaperSummary,
    RepositoryLinkCandidate,
)
from autoad_researcher.paper_intelligence.parser_models import (
    DocumentParseRequest,
    DocumentParseResult,
    PageRange,
    ParseQualityReport,
    ParserManifest,
)
from autoad_researcher.paper_intelligence.tools import (
    CanonicalPaperStore,
    PaperReadResult,
    PaperSearchMatch,
    PaperToolError,
    SectionInfo,
)
from autoad_researcher.paper_intelligence.validator import (
    CandidateValidationIssue,
    ClaimValidationIssue,
    PaperValidationReport,
    validate_candidate,
    validate_candidate_not_selected,
    validate_claim,
    validate_page_index,
)
