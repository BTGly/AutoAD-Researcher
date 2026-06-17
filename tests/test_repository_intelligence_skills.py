"""Tests for Repository Intelligence Skill loading."""

from pathlib import Path

import pytest

from autoad_researcher.repository_intelligence import (
    LoadedSkillsAudit,
    RepositoryIntelligenceRequest,
    SkillLoadError,
    load_required_skill,
    load_skill_file,
    should_load_stage_skill,
    write_loaded_skills,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PROJECT_ROOT / "skills"

ALL_TOOLS = {
    "web_search",
    "web_fetch",
    "github_read",
    "filesystem_list",
    "filesystem_read",
    "filesystem_search",
    "filesystem_stat",
    "process",
}

ALL_PROFILES = {
    "repository_discovery",
    "repository_acquisition",
    "repository_analysis",
    "repository_synthesis",
}


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


def test_loads_mandatory_analysis_skill_with_audit_sha():
    record = load_required_skill(
        stage="analysis",
        skills_root=SKILLS_ROOT,
        registered_tools=ALL_TOOLS,
        permission_profiles=ALL_PROFILES,
    )

    assert record.skill_name == "repository-analysis"
    assert record.permission_profile == "repository_analysis"
    assert record.deferred is True
    assert len(record.skill_sha256) == 64
    assert "source_attested" in record.triggers


def test_skill_body_contains_required_sections():
    skill = load_skill_file(SKILLS_ROOT / "repository-synthesis" / "SKILL.md", relative_to=PROJECT_ROOT)

    assert skill.frontmatter.name == "repository-synthesis"
    assert "## Evidence Requirements" in skill.body


def test_missing_required_tool_blocks_stage():
    tools = ALL_TOOLS - {"filesystem_search"}

    with pytest.raises(SkillLoadError, match="required tools are not registered"):
        load_required_skill(
            stage="analysis",
            skills_root=SKILLS_ROOT,
            registered_tools=tools,
            permission_profiles=ALL_PROFILES,
        )


def test_missing_permission_profile_blocks_stage():
    profiles = ALL_PROFILES - {"repository_acquisition"}

    with pytest.raises(SkillLoadError, match="permission profile is not registered"):
        load_required_skill(
            stage="acquisition",
            skills_root=SKILLS_ROOT,
            registered_tools=ALL_TOOLS,
            permission_profiles=profiles,
        )


def test_explicit_source_skips_discovery_skill():
    assert should_load_stage_skill("discovery", request(repository_url="https://github.com/example/repo")) is False
    assert should_load_stage_skill("discovery", request(local_path="workspace/repos/repo")) is False
    assert should_load_stage_skill("discovery", request()) is True


def test_loaded_skills_audit_refuses_overwrite(tmp_path: Path):
    record = load_required_skill(
        stage="synthesis",
        skills_root=SKILLS_ROOT,
        registered_tools=ALL_TOOLS,
        permission_profiles=ALL_PROFILES,
    )
    audit = LoadedSkillsAudit(schema_version=1, records=[record])
    path = tmp_path / "loaded_skills.json"

    write_loaded_skills(path, audit)

    assert path.is_file()
    with pytest.raises(FileExistsError):
        write_loaded_skills(path, audit)
