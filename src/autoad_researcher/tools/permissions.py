"""Layered permission engine for Tool Foundation."""

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.active_repository_context import IdentifierPattern, Sha256Pattern
from autoad_researcher.tools.contracts import ToolSpec

PermissionDecision = Literal["allow", "ask", "deny"]
RESEARCH_ASSISTANT_STAGES = {"research_assistant", "research_chat"}
RESEARCH_ASSISTANT_FORBIDDEN_TOOLS = {
    "benchmark_run",
    "experiment_execute",
    "experiment_execution",
    "patch_apply",
    "patch_applicator",
    "patch_planner",
    "process",
    "runner_execute",
    "run_pipeline",
}


class PermissionProfile(BaseModel):
    """Stage/profile allow and deny lists."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=IdentifierPattern)
    allow_tools: set[str] = Field(default_factory=set)
    deny_tools: set[str] = Field(default_factory=set)
    ask_tools: set[str] = Field(default_factory=set)


class PermissionRequest(BaseModel):
    """One permission decision request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_call_id: str = Field(pattern=IdentifierPattern)
    tool: ToolSpec
    stage: str = Field(pattern=IdentifierPattern)
    permission_profile: str = Field(pattern=IdentifierPattern)
    arguments_redacted: dict[str, Any] = Field(default_factory=dict)
    skill_sha: str | None = Field(default=None, pattern=Sha256Pattern)
    active_source_id: str | None = Field(default=None, pattern=IdentifierPattern)
    cwd_label: str | None = None


class PermissionDecisionRecord(BaseModel):
    """Permission decision audit record."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_call_id: str = Field(pattern=IdentifierPattern)
    tool_name: str = Field(pattern=IdentifierPattern)
    stage: str = Field(pattern=IdentifierPattern)
    skill_sha: str | None = Field(default=None, pattern=Sha256Pattern)
    arguments_redacted: dict[str, Any]
    permission_decision: PermissionDecision
    matched_rule: str = Field(min_length=1)
    active_source_id: str | None = Field(default=None, pattern=IdentifierPattern)
    cwd_label: str | None = None
    reason: str = Field(min_length=1)


class PermissionEngine(BaseModel):
    """Deterministic layered permission engine."""

    model_config = ConfigDict(extra="forbid")

    global_deny_tools: set[str] = Field(default_factory=set)
    profiles: dict[str, PermissionProfile] = Field(default_factory=dict)
    session_grants: set[str] = Field(default_factory=set)

    def decide(self, request: PermissionRequest) -> PermissionDecisionRecord:
        """Apply global deny, profile rules, and session grants."""
        tool_name = request.tool.name

        if request.stage in RESEARCH_ASSISTANT_STAGES and tool_name in RESEARCH_ASSISTANT_FORBIDDEN_TOOLS:
            return _record(
                request,
                "deny",
                "research_assistant_tool_guard",
                "Research Assistant cannot execute runner, patch, benchmark, experiment, or process tools",
            )

        if tool_name in self.global_deny_tools:
            return _record(request, "deny", "global_deny", "tool globally denied")

        profile = self.profiles.get(request.permission_profile)
        if profile is None:
            return _record(request, "deny", "missing_profile", "permission profile missing")

        if tool_name in profile.deny_tools:
            return _record(request, "deny", f"profile:{profile.name}:deny", "tool denied by profile")

        argv_policy_denial = _repository_process_argv_policy_denial(request)
        if argv_policy_denial is not None:
            matched_rule, reason = argv_policy_denial
            return _record(request, "deny", matched_rule, reason)

        if tool_name in profile.allow_tools:
            return _record(request, "allow", f"profile:{profile.name}:allow", "tool allowed by profile")

        if tool_name in self.session_grants:
            return _record(request, "allow", "session_grant", "tool allowed by session grant")

        if tool_name in profile.ask_tools or request.tool.destructive:
            return _record(request, "ask", f"profile:{profile.name}:ask", "tool requires user approval")

        return _record(request, "deny", f"profile:{profile.name}:default_deny", "tool not allowed by profile")


def default_repository_permission_engine() -> PermissionEngine:
    """Build repository-stage permission profiles from the 3.1 plan."""
    return PermissionEngine(
        global_deny_tools={"repository_write"},
        profiles={
            "repository_discovery": PermissionProfile(
                name="repository_discovery",
                allow_tools={"web_search", "web_fetch", "github_read", "filesystem_read", "filesystem_stat"},
                deny_tools={"process", "repository_write"},
            ),
            "repository_acquisition": PermissionProfile(
                name="repository_acquisition",
                allow_tools={"github_read", "filesystem_stat", "process"},
                deny_tools={"repository_write"},
            ),
            "repository_analysis": PermissionProfile(
                name="repository_analysis",
                allow_tools={"filesystem_list", "filesystem_read", "filesystem_search", "filesystem_stat", "process"},
                deny_tools={"github_read", "web_search", "web_fetch", "repository_write"},
            ),
            "repository_synthesis": PermissionProfile(
                name="repository_synthesis",
                allow_tools=set(),
                deny_tools={"github_read", "web_search", "web_fetch", "process", "repository_write"},
            ),
            "patch_planning": PermissionProfile(
                name="patch_planning",
                allow_tools={"filesystem_list", "filesystem_read", "filesystem_search", "filesystem_stat", "process"},
                deny_tools={"github_read", "web_search", "web_fetch", "repository_write"},
            ),
            "patch_application": PermissionProfile(
                name="patch_application",
                allow_tools={"filesystem_list", "filesystem_read", "filesystem_search", "filesystem_stat", "filesystem_write"},
                deny_tools={"github_read", "web_search", "web_fetch", "process"},
            ),
        },
    )


def append_permission_decision(path: Path, record: PermissionDecisionRecord) -> None:
    """Append one permission decision to `permission_decisions.jsonl`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(record.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True)
    with path.open("ab") as f:
        f.write(data.encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())


def _record(
    request: PermissionRequest,
    decision: PermissionDecision,
    matched_rule: str,
    reason: str,
) -> PermissionDecisionRecord:
    return PermissionDecisionRecord(
        tool_call_id=request.tool_call_id,
        tool_name=request.tool.name,
        stage=request.stage,
        skill_sha=request.skill_sha,
        arguments_redacted=request.arguments_redacted,
        permission_decision=decision,
        matched_rule=matched_rule,
        active_source_id=request.active_source_id,
        cwd_label=request.cwd_label,
        reason=reason,
    )


def _repository_process_argv_policy_denial(request: PermissionRequest) -> tuple[str, str] | None:
    if request.tool.name != "process":
        return None
    if request.permission_profile not in {"repository_acquisition", "repository_analysis"}:
        return None

    argv = request.arguments_redacted.get("argv")
    if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
        return ("argv_policy:invalid", "process argv must be an audited string list")
    if len(argv) < 2 or argv[0] != "git":
        return (f"argv_policy:{request.permission_profile}", "repository process calls must use git argv")
    if "submodule" in argv or "lfs" in argv:
        return (f"argv_policy:{request.permission_profile}", "git submodule and lfs operations are forbidden")

    subcommand = argv[1]
    if request.permission_profile == "repository_acquisition":
        return _acquisition_git_denial(argv, subcommand)
    return _analysis_git_denial(argv, subcommand)


def _acquisition_git_denial(argv: list[str], subcommand: str) -> tuple[str, str] | None:
    matched_rule = "argv_policy:repository_acquisition"
    allowed = {"init", "clone", "remote", "fetch", "checkout", "status", "rev-parse", "symbolic-ref"}
    if subcommand not in allowed:
        return (matched_rule, f"git subcommand not allowed during acquisition: {subcommand}")
    if subcommand == "remote" and (len(argv) < 3 or argv[2] not in {"add", "get-url"}):
        return (matched_rule, "git remote is limited to add/get-url during acquisition")
    if subcommand == "checkout" and "--detach" not in argv:
        return (matched_rule, "git checkout must use --detach during acquisition")
    if subcommand == "status" and "--porcelain" not in argv:
        return (matched_rule, "git status must use --porcelain during acquisition")
    if subcommand == "config":
        return (matched_rule, "git config is forbidden")
    return None


def _analysis_git_denial(argv: list[str], subcommand: str) -> tuple[str, str] | None:
    matched_rule = "argv_policy:repository_analysis"
    allowed = {"status", "rev-parse", "ls-files", "grep", "show", "log", "diff"}
    if subcommand not in allowed:
        return (matched_rule, f"git subcommand not allowed during analysis: {subcommand}")
    if subcommand == "status" and "--porcelain" not in argv:
        return (matched_rule, "git status must use --porcelain during analysis")
    if subcommand == "diff" and "--no-ext-diff" not in argv:
        return (matched_rule, "git diff must use --no-ext-diff during analysis")
    return None
