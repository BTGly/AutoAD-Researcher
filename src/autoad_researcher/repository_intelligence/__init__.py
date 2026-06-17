"""Repository Intelligence contracts and deterministic helpers."""

from autoad_researcher.repository_intelligence.control_models import AnalysisControlSignal
from autoad_researcher.repository_intelligence.evidence_models import (
    EvidenceIndexRecord,
    EvidenceRef,
    FileEvidenceRef,
    RepositoryIdentityEvidenceRef,
    UserInputEvidenceRef,
    WebEvidenceRef,
)
from autoad_researcher.repository_intelligence.models import (
    RepositoryAgentBudget,
    RepositoryArtifactPaths,
    RepositoryCandidate,
    RepositoryClaim,
    RepositoryIntelligenceRequest,
    RepositoryIntelligenceResult,
    RepositoryResolution,
    RepositorySource,
    SubmoduleRecord,
)
from autoad_researcher.repository_intelligence.skills import (
    LoadedSkillRecord,
    LoadedSkillsAudit,
    RepositorySkill,
    RepositorySkillFrontmatter,
    SkillLoadError,
    load_required_skill,
    load_skill_file,
    should_load_stage_skill,
    write_loaded_skills,
)

__all__ = [
    "AnalysisControlSignal",
    "EvidenceIndexRecord",
    "EvidenceRef",
    "FileEvidenceRef",
    "RepositoryAgentBudget",
    "RepositoryArtifactPaths",
    "RepositoryCandidate",
    "RepositoryClaim",
    "RepositoryIdentityEvidenceRef",
    "RepositoryIntelligenceRequest",
    "RepositoryIntelligenceResult",
    "RepositoryResolution",
    "RepositorySource",
    "LoadedSkillRecord",
    "LoadedSkillsAudit",
    "RepositorySkill",
    "RepositorySkillFrontmatter",
    "SkillLoadError",
    "SubmoduleRecord",
    "UserInputEvidenceRef",
    "WebEvidenceRef",
    "load_required_skill",
    "load_skill_file",
    "should_load_stage_skill",
    "write_loaded_skills",
]
