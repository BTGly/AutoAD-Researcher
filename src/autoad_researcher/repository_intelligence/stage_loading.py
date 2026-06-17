"""Stage-driven mandatory Skill loading for Repository Intelligence."""

from pathlib import Path

from autoad_researcher.repository_intelligence.models import RepositoryIntelligenceRequest
from autoad_researcher.repository_intelligence.skills import (
    LoadedSkillRecord,
    LoadedSkillsAudit,
    RepositoryStage,
    load_required_skill,
    should_load_stage_skill,
    write_loaded_skills,
)
from autoad_researcher.tools import PermissionEngine, ToolRegistry


class StageSkillLoadResult:
    """Result of attempting to load a stage mandatory Skill."""

    def __init__(self, *, record: LoadedSkillRecord | None, skipped: bool):
        self.record = record
        self.skipped = skipped


def load_stage_skill(
    *,
    stage: RepositoryStage,
    request: RepositoryIntelligenceRequest,
    skills_root: Path,
    tool_registry: ToolRegistry,
    permission_engine: PermissionEngine,
    audit_path: Path | None = None,
) -> StageSkillLoadResult:
    """Load a mandatory Skill for a stage when stage/request rules require it."""
    if not should_load_stage_skill(stage, request):
        return StageSkillLoadResult(record=None, skipped=True)

    record = load_required_skill(
        stage=stage,
        skills_root=skills_root,
        registered_tools=set(tool_registry.tools),
        permission_profiles=set(permission_engine.profiles),
    )
    if audit_path is not None:
        write_loaded_skills(audit_path, LoadedSkillsAudit(schema_version=1, records=[record]))
    return StageSkillLoadResult(record=record, skipped=False)
