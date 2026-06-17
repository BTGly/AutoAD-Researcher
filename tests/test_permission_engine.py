"""Tests for layered permission engine."""

import json
from pathlib import Path

from autoad_researcher.tools import (
    PermissionEngine,
    PermissionProfile,
    PermissionRequest,
    ToolSpec,
    append_permission_decision,
    default_repository_permission_engine,
)


def spec(name: str, *, destructive: bool = False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        read_only=not destructive,
        destructive=destructive,
        concurrency_safe=True,
        deferred=False,
        permission_category="generic",
    )


def request(tool: ToolSpec, *, profile: str = "repository_analysis") -> PermissionRequest:
    return PermissionRequest(
        tool_call_id="tool_001",
        tool=tool,
        stage="analysis",
        permission_profile=profile,
        arguments_redacted={"path": "README.md"},
        active_source_id="source_001",
        cwd_label="workspace/repos/source_001",
    )


def test_global_deny_wins_before_profile_allow():
    engine = PermissionEngine(
        global_deny_tools={"process"},
        profiles={
            "repository_analysis": PermissionProfile(
                name="repository_analysis",
                allow_tools={"process"},
            )
        },
    )

    decision = engine.decide(request(spec("process")))

    assert decision.permission_decision == "deny"
    assert decision.matched_rule == "global_deny"


def test_repository_discovery_denies_process():
    engine = default_repository_permission_engine()

    decision = engine.decide(request(spec("process"), profile="repository_discovery"))

    assert decision.permission_decision == "deny"
    assert decision.matched_rule == "profile:repository_discovery:deny"


def test_repository_analysis_denies_web_and_allows_filesystem_read():
    engine = default_repository_permission_engine()

    web = engine.decide(request(spec("web_search"), profile="repository_analysis"))
    fs = engine.decide(request(spec("filesystem_read"), profile="repository_analysis"))

    assert web.permission_decision == "deny"
    assert fs.permission_decision == "allow"


def test_missing_profile_denies():
    engine = default_repository_permission_engine()

    decision = engine.decide(request(spec("filesystem_read"), profile="missing_profile"))

    assert decision.permission_decision == "deny"
    assert decision.matched_rule == "missing_profile"


def test_session_grant_can_allow_unlisted_tool():
    engine = PermissionEngine(
        profiles={"repository_analysis": PermissionProfile(name="repository_analysis")},
        session_grants={"custom_probe"},
    )

    decision = engine.decide(request(spec("custom_probe")))

    assert decision.permission_decision == "allow"
    assert decision.matched_rule == "session_grant"


def test_destructive_tool_requires_ask_when_not_allowed_or_denied():
    engine = PermissionEngine(
        profiles={"repository_analysis": PermissionProfile(name="repository_analysis")},
    )

    decision = engine.decide(request(spec("danger_tool", destructive=True)))

    assert decision.permission_decision == "ask"


def test_append_permission_decision_jsonl(tmp_path: Path):
    engine = default_repository_permission_engine()
    decision = engine.decide(request(spec("filesystem_read"), profile="repository_analysis"))
    path = tmp_path / "permission_decisions.jsonl"

    append_permission_decision(path, decision)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["tool_call_id"] == "tool_001"
    assert rows[0]["permission_decision"] == "allow"
