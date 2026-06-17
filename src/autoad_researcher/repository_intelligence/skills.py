"""Repository Intelligence Skill loading and audit contracts."""

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoad_researcher.repository_intelligence.ids import IdentifierPattern, Sha256Pattern
from autoad_researcher.repository_intelligence.models import RepositoryIntelligenceRequest

RepositoryStage = Literal["discovery", "acquisition", "analysis", "synthesis", "repair"]

STAGE_REQUIRED_SKILLS: dict[str, str] = {
    "discovery": "repository-discovery",
    "acquisition": "repository-acquisition",
    "analysis": "repository-analysis",
    "synthesis": "repository-synthesis",
}

STAGE_REQUIRED_TRIGGERS: dict[str, str] = {
    "discovery": "source_missing",
    "acquisition": "repository_resolved",
    "analysis": "source_attested",
    "synthesis": "analysis_synthesis_ready",
}

REQUIRED_SKILL_SECTIONS = [
    "Purpose",
    "Preconditions",
    "Allowed Tools",
    "Forbidden Actions",
    "Recommended Workflow",
    "Evidence Requirements",
    "Output Contract",
    "Stop Conditions",
    "Failure Handling",
    "Examples",
]


class SkillLoadError(ValueError):
    """Raised when a mandatory Repository Skill cannot be loaded."""


class RepositorySkillFrontmatter(BaseModel):
    """Validated frontmatter for a Repository Intelligence Skill."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=IdentifierPattern)
    description: str = Field(min_length=1)
    required_tools: list[str] = Field(default_factory=list)
    permission_profile: str = Field(pattern=IdentifierPattern)
    max_context_tokens: int = Field(gt=0)
    deferred: bool
    triggers: list[str] = Field(min_length=1)

    @field_validator("required_tools", "triggers")
    @classmethod
    def _validate_list_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate values are not allowed")
        return value


class RepositorySkill(BaseModel):
    """A parsed Repository Intelligence Skill file."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    frontmatter: RepositorySkillFrontmatter
    body: str = Field(min_length=1)
    skill_sha256: str = Field(pattern=Sha256Pattern)
    relative_path: str

    @model_validator(mode="after")
    def _validate_required_sections(self):
        missing = [section for section in REQUIRED_SKILL_SECTIONS if f"## {section}" not in self.body]
        if missing:
            raise ValueError(f"skill body missing required sections: {missing}")
        return self


class LoadedSkillRecord(BaseModel):
    """Audit record for a mandatory loaded Skill."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    stage: RepositoryStage
    skill_name: str = Field(pattern=IdentifierPattern)
    permission_profile: str = Field(pattern=IdentifierPattern)
    required_tools: list[str]
    triggers: list[str]
    deferred: bool
    skill_sha256: str = Field(pattern=Sha256Pattern)
    relative_path: str


class LoadedSkillsAudit(BaseModel):
    """`loaded_skills.json` payload."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    records: list[LoadedSkillRecord]


def should_load_stage_skill(stage: str, request: RepositoryIntelligenceRequest) -> bool:
    """Return whether a stage needs its mandatory Skill loaded."""
    if stage == "discovery" and (request.repository_url or request.local_path):
        return False
    return stage in STAGE_REQUIRED_SKILLS


def load_required_skill(
    *,
    stage: RepositoryStage,
    skills_root: Path,
    registered_tools: set[str],
    permission_profiles: set[str],
) -> LoadedSkillRecord:
    """Load and validate the mandatory Skill for a stage."""
    skill_name = STAGE_REQUIRED_SKILLS.get(stage)
    if skill_name is None:
        raise SkillLoadError(f"no required skill configured for stage: {stage}")

    skill = load_skill_file(skills_root / skill_name / "SKILL.md", relative_to=skills_root.parent)
    frontmatter = skill.frontmatter

    if frontmatter.name != skill_name:
        raise SkillLoadError(f"skill name mismatch: expected {skill_name}, got {frontmatter.name}")

    trigger = STAGE_REQUIRED_TRIGGERS[stage]
    if trigger not in frontmatter.triggers:
        raise SkillLoadError(f"skill {skill_name} missing stage trigger: {trigger}")

    missing_tools = sorted(set(frontmatter.required_tools) - registered_tools)
    if missing_tools:
        raise SkillLoadError(f"required tools are not registered: {missing_tools}")

    if frontmatter.permission_profile not in permission_profiles:
        raise SkillLoadError(f"permission profile is not registered: {frontmatter.permission_profile}")

    return LoadedSkillRecord(
        schema_version=1,
        stage=stage,
        skill_name=frontmatter.name,
        permission_profile=frontmatter.permission_profile,
        required_tools=frontmatter.required_tools,
        triggers=frontmatter.triggers,
        deferred=frontmatter.deferred,
        skill_sha256=skill.skill_sha256,
        relative_path=skill.relative_path,
    )


def load_skill_file(path: Path, *, relative_to: Path | None = None) -> RepositorySkill:
    """Parse one `SKILL.md` file with YAML frontmatter."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise SkillLoadError(f"skill frontmatter missing: {path}")

    try:
        _, frontmatter_raw, body = raw.split("---\n", 2)
    except ValueError as exc:
        raise SkillLoadError(f"skill frontmatter is not closed: {path}") from exc

    frontmatter_data = yaml.safe_load(frontmatter_raw)
    if not isinstance(frontmatter_data, dict):
        raise SkillLoadError(f"skill frontmatter must be a mapping: {path}")

    skill_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    relative_path = path.as_posix() if relative_to is None else path.relative_to(relative_to).as_posix()
    return RepositorySkill(
        frontmatter=RepositorySkillFrontmatter.model_validate(frontmatter_data),
        body=body,
        skill_sha256=skill_sha,
        relative_path=relative_path,
    )


def write_loaded_skills(path: Path, audit: LoadedSkillsAudit) -> None:
    """Atomically write a loaded Skills audit file. Refuses to overwrite."""
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        data = json.dumps(
            audit.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
