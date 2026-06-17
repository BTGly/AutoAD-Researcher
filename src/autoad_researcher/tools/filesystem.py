"""Workspace-scoped filesystem tools."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.repository_intelligence.ids import IdentifierPattern, validate_relative_path
from autoad_researcher.tools.contracts import ToolSpec
from autoad_researcher.tools.permissions import PermissionDecisionRecord, PermissionEngine, PermissionRequest


class FilesystemToolError(ValueError):
    """Raised when a filesystem request violates workspace scope."""


class FilesystemRequest(BaseModel):
    """Base request for workspace-scoped filesystem tools."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_call_id: str = Field(pattern=IdentifierPattern)
    workspace_root: Path
    workspace_label: str
    path: str
    stage: str = Field(pattern=IdentifierPattern)
    permission_profile: str = Field(pattern=IdentifierPattern)
    skill_sha: str | None = None
    active_source_id: str | None = Field(default=None, pattern=IdentifierPattern)

    @field_validator("workspace_label", "path")
    @classmethod
    def _validate_relative_paths(cls, value: str) -> str:
        return validate_relative_path(value)


class FilesystemReadRequest(FilesystemRequest):
    max_bytes: int = Field(default=65536, gt=0)


class FilesystemSearchRequest(FilesystemRequest):
    pattern: str = Field(min_length=1)
    max_matches: int = Field(default=100, gt=0)


class FilesystemEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: Literal["file", "directory", "other"]
    size_bytes: int | None = None


class FilesystemStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: Literal["file", "directory", "other"]
    size_bytes: int | None = None


class FilesystemSearchMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    line_number: int
    line: str


class FilesystemToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "blocked", "not_found"]
    permission: PermissionDecisionRecord
    entries: list[FilesystemEntry] = Field(default_factory=list)
    text: str | None = None
    stat: FilesystemStat | None = None
    matches: list[FilesystemSearchMatch] = Field(default_factory=list)


def filesystem_tool_spec(name: str) -> ToolSpec:
    """Return one filesystem ToolSpec."""
    return ToolSpec(
        name=name,
        description=f"Workspace-scoped {name} tool.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        read_only=True,
        destructive=False,
        concurrency_safe=True,
        deferred=False,
        permission_category="filesystem",
    )


def filesystem_list(request: FilesystemRequest, *, permission_engine: PermissionEngine) -> FilesystemToolResult:
    permission = _permission("filesystem_list", request, permission_engine)
    if permission.permission_decision != "allow":
        return FilesystemToolResult(status="blocked", permission=permission)
    target = _resolve_workspace_path(request.workspace_root, request.path)
    if not target.exists():
        return FilesystemToolResult(status="not_found", permission=permission)
    entries = [_entry(path, request.workspace_root.resolve()) for path in sorted(target.iterdir())]
    return FilesystemToolResult(status="success", permission=permission, entries=entries)


def filesystem_read(request: FilesystemReadRequest, *, permission_engine: PermissionEngine) -> FilesystemToolResult:
    permission = _permission("filesystem_read", request, permission_engine)
    if permission.permission_decision != "allow":
        return FilesystemToolResult(status="blocked", permission=permission)
    target = _resolve_workspace_path(request.workspace_root, request.path)
    if not target.is_file():
        return FilesystemToolResult(status="not_found", permission=permission)
    data = target.read_bytes()[: request.max_bytes]
    return FilesystemToolResult(
        status="success",
        permission=permission,
        text=data.decode("utf-8", errors="replace"),
    )


def filesystem_stat(request: FilesystemRequest, *, permission_engine: PermissionEngine) -> FilesystemToolResult:
    permission = _permission("filesystem_stat", request, permission_engine)
    if permission.permission_decision != "allow":
        return FilesystemToolResult(status="blocked", permission=permission)
    target = _resolve_workspace_path(request.workspace_root, request.path)
    if not target.exists():
        return FilesystemToolResult(status="not_found", permission=permission)
    return FilesystemToolResult(status="success", permission=permission, stat=_stat(target, request.workspace_root.resolve()))


def filesystem_search(request: FilesystemSearchRequest, *, permission_engine: PermissionEngine) -> FilesystemToolResult:
    permission = _permission("filesystem_search", request, permission_engine)
    if permission.permission_decision != "allow":
        return FilesystemToolResult(status="blocked", permission=permission)
    target = _resolve_workspace_path(request.workspace_root, request.path)
    if not target.exists():
        return FilesystemToolResult(status="not_found", permission=permission)

    files = [target] if target.is_file() else sorted(path for path in target.rglob("*") if path.is_file())
    matches: list[FilesystemSearchMatch] = []
    root = request.workspace_root.resolve()
    for file_path in files:
        _reject_symlink_components(root, file_path)
        for line_number, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if request.pattern in line:
                matches.append(
                    FilesystemSearchMatch(
                        path=file_path.resolve().relative_to(root).as_posix(),
                        line_number=line_number,
                        line=line,
                    )
                )
                if len(matches) >= request.max_matches:
                    return FilesystemToolResult(status="success", permission=permission, matches=matches)
    return FilesystemToolResult(status="success", permission=permission, matches=matches)


def _permission(name: str, request: FilesystemRequest, engine: PermissionEngine) -> PermissionDecisionRecord:
    return engine.decide(
        PermissionRequest(
            tool_call_id=request.tool_call_id,
            tool=filesystem_tool_spec(name),
            stage=request.stage,
            permission_profile=request.permission_profile,
            arguments_redacted={"path": request.path, "workspace_label": request.workspace_label},
            skill_sha=request.skill_sha,
            active_source_id=request.active_source_id,
            cwd_label=request.workspace_label,
        )
    )


def _resolve_workspace_path(root: Path, relative_path: str) -> Path:
    workspace_root = root.resolve()
    candidate = workspace_root / validate_relative_path(relative_path)
    _reject_symlink_components(workspace_root, candidate)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise FilesystemToolError(f"workspace path escape: {relative_path}") from exc
    return resolved


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise FilesystemToolError(f"symlink path forbidden: {current.relative_to(root).as_posix()}")


def _entry(path: Path, root: Path) -> FilesystemEntry:
    stat = _stat(path, root)
    return FilesystemEntry(path=stat.path, kind=stat.kind, size_bytes=stat.size_bytes)


def _stat(path: Path, root: Path) -> FilesystemStat:
    if path.is_file():
        kind = "file"
        size = path.stat().st_size
    elif path.is_dir():
        kind = "directory"
        size = None
    else:
        kind = "other"
        size = None
    return FilesystemStat(path=path.resolve().relative_to(root).as_posix(), kind=kind, size_bytes=size)
