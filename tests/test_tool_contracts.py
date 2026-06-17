"""Tests for generic Tool Foundation contracts."""

import pytest
from pydantic import ValidationError

from autoad_researcher.tools import ActiveRepositoryContext, ToolContext, ToolRegistry, ToolResult, ToolSpec


def tool_spec(**overrides) -> ToolSpec:
    data = {
        "name": "filesystem_read",
        "description": "Read one workspace-scoped file.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "read_only": True,
        "destructive": False,
        "concurrency_safe": True,
        "deferred": False,
        "permission_category": "filesystem",
    }
    data.update(overrides)
    return ToolSpec(**data)


def test_tool_spec_accepts_plan_fields():
    spec = tool_spec()

    assert spec.name == "filesystem_read"
    assert spec.read_only is True


def test_tool_spec_rejects_extra_field():
    with pytest.raises(ValidationError):
        tool_spec(extra_field="not allowed")


def test_tool_spec_rejects_read_only_destructive_combo():
    with pytest.raises(ValidationError, match="read_only tools cannot be destructive"):
        tool_spec(destructive=True)


def test_tool_registry_registers_and_requires_specs():
    registry = ToolRegistry().register(tool_spec()).register(
        tool_spec(
            name="web_search",
            description="Deferred search provider.",
            deferred=True,
            permission_category="web",
        )
    )

    assert registry.get("filesystem_read").permission_category == "filesystem"
    registry.require({"filesystem_read", "web_search"})
    assert registry.deferred_tool_names() == ["web_search"]


def test_tool_registry_rejects_duplicate_spec():
    registry = ToolRegistry().register(tool_spec())

    with pytest.raises(ValueError, match="duplicate tool spec"):
        registry.register(tool_spec())


def test_tool_registry_key_must_match_spec_name():
    with pytest.raises(ValidationError, match="tool registry key must match"):
        ToolRegistry(tools={"wrong": tool_spec(name="right")})


def test_tool_context_seals_active_repository_field():
    context = ToolContext(
        active_repository=ActiveRepositoryContext(
            source_id="source_001",
            repository_root="/tmp/repo",
            resolved_commit="a" * 40,
            tree_sha="b" * 64,
        )
    )

    assert context.active_repository is not None
    assert context.active_repository.source_id == "source_001"


def test_tool_context_rejects_unsealed_extra_fields():
    with pytest.raises(ValidationError):
        ToolContext(stage="analysis")


def test_tool_result_preserves_unsealed_extension_fields():
    result = ToolResult(status="success", payload={"path": "README.md"})

    assert result.model_extra["payload"]["path"] == "README.md"
