"""Tests for deferred tool loading."""

from autoad_researcher.tools import ToolRegistry, ToolSpec, initial_tool_specs, load_stage_tool_specs


def spec(name: str, *, deferred: bool, permission_category: str = "generic") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        read_only=True,
        destructive=False,
        concurrency_safe=True,
        deferred=deferred,
        permission_category=permission_category,
    )


def registry() -> ToolRegistry:
    r = ToolRegistry()
    for tool in [
        spec("filesystem_list", deferred=False, permission_category="filesystem"),
        spec("filesystem_read", deferred=False, permission_category="filesystem"),
        spec("filesystem_search", deferred=False, permission_category="filesystem"),
        spec("filesystem_stat", deferred=False, permission_category="filesystem"),
        spec("process", deferred=False, permission_category="process"),
        spec("web_search", deferred=True, permission_category="web"),
        spec("web_fetch", deferred=True, permission_category="web"),
        spec("github_read", deferred=True, permission_category="github"),
    ]:
        r = r.register(tool)
    return r


def names(specs: list[ToolSpec]) -> list[str]:
    return [s.name for s in specs]


def test_initial_context_excludes_deferred_tools():
    loaded = initial_tool_specs(registry())

    assert names(loaded) == [
        "filesystem_list",
        "filesystem_read",
        "filesystem_search",
        "filesystem_stat",
        "process",
    ]


def test_discovery_stage_loads_discovery_deferred_tools():
    loaded = load_stage_tool_specs(
        registry=registry(),
        stage="discovery",
        trigger_reason="stage_entry",
        loaded_at="2026-06-17T00:00:00Z",
    )

    assert names(loaded.specs) == ["github_read", "web_fetch", "web_search"]
    assert {r.tool_name for r in loaded.audit_records} == {"github_read", "web_fetch", "web_search"}
    assert all(len(r.schema_sha256) == 64 for r in loaded.audit_records)


def test_analysis_stage_does_not_load_web_or_github_tools():
    loaded = load_stage_tool_specs(
        registry=registry(),
        stage="analysis",
        trigger_reason="stage_entry",
        loaded_at="2026-06-17T00:00:00Z",
    )

    assert names(loaded.specs) == [
        "filesystem_list",
        "filesystem_read",
        "filesystem_search",
        "filesystem_stat",
        "process",
    ]


def test_synthesis_stage_loads_no_new_tools():
    loaded = load_stage_tool_specs(
        registry=registry(),
        stage="synthesis",
        trigger_reason="stage_entry",
        loaded_at="2026-06-17T00:00:00Z",
    )

    assert loaded.specs == []
    assert loaded.audit_records == []
