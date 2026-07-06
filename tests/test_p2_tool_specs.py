"""Tests for optional P2 tool specs."""

from autoad_researcher.repository_intelligence.harness import default_repository_tool_registry
from autoad_researcher.tools import git_clone_tool_spec, web_fetch_tool_spec, web_search_tool_spec
from autoad_researcher.tools.deferred import load_stage_tool_specs
from autoad_researcher.tools.permissions import PermissionRequest, default_repository_permission_engine


def test_web_search_tool_spec_schema_matches_plan():
    spec = web_search_tool_spec()

    assert spec.name == "web_search"
    assert spec.deferred is True
    assert spec.read_only is True
    assert "candidate sources" in spec.description
    assert {"query", "type", "livecrawl", "numResults", "contextMaxCharacters"} <= set(spec.input_schema["properties"])


def test_web_fetch_tool_spec_wraps_secure_provider_contract():
    spec = web_fetch_tool_spec()

    assert spec.name == "web_fetch"
    assert spec.deferred is True
    assert spec.permission_category == "web"
    assert "SecureWebFetchProvider" in spec.description
    assert {"url", "format", "timeout"} <= set(spec.input_schema["properties"])


def test_git_clone_tool_spec_is_read_only_acquisition_not_execution():
    spec = git_clone_tool_spec()

    assert spec.name == "git_clone"
    assert spec.deferred is True
    assert spec.destructive is False
    assert spec.permission_category == "repository_acquisition"
    assert "do not commit" in spec.description
    assert "push" in spec.description
    assert "run project code" in spec.description


def test_acquisition_stage_loads_git_clone_tool_spec():
    loaded = load_stage_tool_specs(
        registry=default_repository_tool_registry(),
        stage="acquisition",
        trigger_reason="stage_entry",
        loaded_at="2026-07-05T00:00:00Z",
    )

    assert "git_clone" in {spec.name for spec in loaded.specs}
    assert "git_clone" in {record.tool_name for record in loaded.audit_records}


def test_repository_acquisition_permission_allows_git_clone():
    spec = git_clone_tool_spec()
    decision = default_repository_permission_engine().decide(
        PermissionRequest(
            tool_call_id="tool_001",
            tool=spec,
            stage="acquisition",
            permission_profile="repository_acquisition",
            arguments_redacted={"acquisition_profile": "shallow_ref"},
        )
    )

    assert decision.permission_decision == "allow"
