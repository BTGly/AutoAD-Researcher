"""Tests for workspace-scoped filesystem tools."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.tools import (
    FilesystemReadRequest,
    FilesystemRequest,
    FilesystemSearchRequest,
    FilesystemToolError,
    PermissionEngine,
    PermissionProfile,
    filesystem_list,
    filesystem_read,
    filesystem_search,
    filesystem_stat,
)


def allow_fs_engine() -> PermissionEngine:
    return PermissionEngine(
        profiles={
            "repository_analysis": PermissionProfile(
                name="repository_analysis",
                allow_tools={"filesystem_list", "filesystem_read", "filesystem_search", "filesystem_stat"},
            )
        }
    )


def deny_fs_engine() -> PermissionEngine:
    return PermissionEngine(
        profiles={
            "repository_analysis": PermissionProfile(
                name="repository_analysis",
                deny_tools={"filesystem_read"},
            )
        }
    )


def base_request(tmp_path: Path, path: str = ".") -> FilesystemRequest:
    return FilesystemRequest(
        tool_call_id="tool_001",
        workspace_root=tmp_path,
        workspace_label="workspace",
        path=path,
        stage="analysis",
        permission_profile="repository_analysis",
    )


def test_filesystem_list_stat_and_read_within_workspace(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")

    listed = filesystem_list(base_request(tmp_path, "src"), permission_engine=allow_fs_engine())
    stat = filesystem_stat(base_request(tmp_path, "src/main.py"), permission_engine=allow_fs_engine())
    read = filesystem_read(
        FilesystemReadRequest(**base_request(tmp_path, "src/main.py").model_dump()),
        permission_engine=allow_fs_engine(),
    )

    assert listed.status == "success"
    assert listed.entries[0].path == "src/main.py"
    assert stat.stat is not None
    assert stat.stat.kind == "file"
    assert read.text == "print('hello')\n"


def test_filesystem_search_finds_literal_matches(tmp_path: Path):
    (tmp_path / "README.md").write_text("train command\nother line\n", encoding="utf-8")

    result = filesystem_search(
        FilesystemSearchRequest(**base_request(tmp_path, "README.md").model_dump(), pattern="train"),
        permission_engine=allow_fs_engine(),
    )

    assert result.status == "success"
    assert result.matches[0].path == "README.md"
    assert result.matches[0].line_number == 1


def test_filesystem_request_rejects_parent_traversal(tmp_path: Path):
    with pytest.raises(ValidationError, match="parent traversal forbidden"):
        base_request(tmp_path, "../outside")


def test_filesystem_tool_rejects_symlink_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside_fs_target"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside)

    with pytest.raises(FilesystemToolError, match="symlink path forbidden"):
        filesystem_read(
            FilesystemReadRequest(**base_request(tmp_path, "link/secret.txt").model_dump()),
            permission_engine=allow_fs_engine(),
        )


def test_filesystem_read_blocks_when_permission_denied(tmp_path: Path):
    (tmp_path / "README.md").write_text("x", encoding="utf-8")

    result = filesystem_read(
        FilesystemReadRequest(**base_request(tmp_path, "README.md").model_dump()),
        permission_engine=deny_fs_engine(),
    )

    assert result.status == "blocked"
    assert result.permission.permission_decision == "deny"
