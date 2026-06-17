"""Tests for stage-driven mandatory Skill loading."""

from pathlib import Path

import pytest

from autoad_researcher.repository_intelligence import (
    RepositoryIntelligenceRequest,
    SkillLoadError,
)
from autoad_researcher.repository_intelligence.stage_loading import load_stage_skill
from autoad_researcher.tools import PermissionEngine, PermissionProfile, ToolRegistry, ToolSpec

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PROJECT_ROOT / "skills"


def request(**overrides) -> RepositoryIntelligenceRequest:
    data = {
        "schema_version": 1,
        "request_id": "req_001",
        "run_id": "run_demo",
        "user_goal": "analyze repository",
        "discovery_allowed": True,
        "user_confirmation_policy": "when_ambiguous",
        "budget_profile": "small",
    }
    data.update(overrides)
    return RepositoryIntelligenceRequest(**data)


def spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        read_only=True,
        destructive=False,
        concurrency_safe=True,
        deferred=False,
        permission_category="generic",
    )


def registry(*names: str) -> ToolRegistry:
    r = ToolRegistry()
    for name in names:
        r = r.register(spec(name))
    return r


def permission_engine(*profiles: str) -> PermissionEngine:
    return PermissionEngine(
        profiles={name: PermissionProfile(name=name) for name in profiles}
    )


def test_stage_loader_loads_analysis_skill_and_writes_audit(tmp_path: Path):
    result = load_stage_skill(
        stage="analysis",
        request=request(),
        skills_root=SKILLS_ROOT,
        tool_registry=registry("filesystem_list", "filesystem_read", "filesystem_search", "filesystem_stat", "process"),
        permission_engine=permission_engine("repository_analysis"),
        audit_path=tmp_path / "loaded_skills.json",
    )

    assert result.skipped is False
    assert result.record is not None
    assert result.record.skill_name == "repository-analysis"
    assert (tmp_path / "loaded_skills.json").is_file()


def test_stage_loader_skips_discovery_for_explicit_source():
    result = load_stage_skill(
        stage="discovery",
        request=request(repository_url="https://github.com/example/repo"),
        skills_root=SKILLS_ROOT,
        tool_registry=registry("web_search", "web_fetch", "github_read", "filesystem_read", "filesystem_stat"),
        permission_engine=permission_engine("repository_discovery"),
    )

    assert result.skipped is True
    assert result.record is None


def test_stage_loader_blocks_when_registry_missing_required_tool():
    with pytest.raises(SkillLoadError, match="required tools are not registered"):
        load_stage_skill(
            stage="analysis",
            request=request(),
            skills_root=SKILLS_ROOT,
            tool_registry=registry("filesystem_list", "filesystem_read", "filesystem_stat", "process"),
            permission_engine=permission_engine("repository_analysis"),
        )


def test_stage_loader_blocks_when_profile_missing():
    with pytest.raises(SkillLoadError, match="permission profile is not registered"):
        load_stage_skill(
            stage="analysis",
            request=request(),
            skills_root=SKILLS_ROOT,
            tool_registry=registry("filesystem_list", "filesystem_read", "filesystem_search", "filesystem_stat", "process"),
            permission_engine=permission_engine("repository_discovery"),
        )
