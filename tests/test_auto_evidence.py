"""Tests for automatic filesystem evidence middleware."""

from pathlib import Path

from autoad_researcher.repository_intelligence import ActiveRepositoryContext, read_evidence_index
from autoad_researcher.repository_intelligence.auto_evidence import (
    filesystem_read_with_evidence,
    filesystem_search_with_evidence,
)
from autoad_researcher.tools import (
    FilesystemReadRequest,
    FilesystemSearchRequest,
    PermissionEngine,
    PermissionProfile,
    ToolContext,
)


def allow_fs_engine() -> PermissionEngine:
    return PermissionEngine(
        profiles={
            "repository_analysis": PermissionProfile(
                name="repository_analysis",
                allow_tools={"filesystem_read", "filesystem_search"},
            )
        }
    )


def context(repo: Path) -> ToolContext:
    return ToolContext(
        active_repository=ActiveRepositoryContext(
            source_id="source_001",
            repository_root=repo,
            resolved_commit="a" * 40,
            tree_sha="b" * 64,
        )
    )


def read_request(repo: Path, path: str, *, active: bool = True) -> FilesystemReadRequest:
    return FilesystemReadRequest(
        tool_call_id="tool_read",
        workspace_root=repo,
        workspace_label="workspace/repos/source_001",
        path=path,
        stage="analysis",
        permission_profile="repository_analysis",
        active_source_id="source_001" if active else None,
        tool_context=context(repo) if active else None,
    )


def search_request(repo: Path, path: str) -> FilesystemSearchRequest:
    return FilesystemSearchRequest(
        tool_call_id="tool_search",
        workspace_root=repo,
        workspace_label="workspace/repos/source_001",
        path=path,
        pattern="train",
        stage="analysis",
        permission_profile="repository_analysis",
        active_source_id="source_001",
        tool_context=context(repo),
    )


def test_filesystem_read_with_active_context_appends_file_evidence(tmp_path: Path):
    (tmp_path / "README.md").write_text("line one\nline two\n", encoding="utf-8")
    index = tmp_path / "evidence_index.jsonl"

    result = filesystem_read_with_evidence(
        read_request(tmp_path, "README.md"),
        permission_engine=allow_fs_engine(),
        evidence_index_path=index,
        evidence_id="ev_read",
    )

    assert result.tool_result.status == "success"
    assert len(result.evidence) == 1
    assert result.evidence[0].path == "README.md"
    assert result.evidence[0].start_line == 1
    assert result.evidence[0].end_line == 2
    assert read_evidence_index(index)[0].evidence.evidence_id == "ev_read"


def test_filesystem_read_without_active_context_generates_no_repository_evidence(tmp_path: Path):
    (tmp_path / "README.md").write_text("line one\n", encoding="utf-8")
    index = tmp_path / "evidence_index.jsonl"

    result = filesystem_read_with_evidence(
        read_request(tmp_path, "README.md", active=False),
        permission_engine=allow_fs_engine(),
        evidence_index_path=index,
        evidence_id="ev_read",
    )

    assert result.tool_result.status == "success"
    assert result.evidence == []
    assert read_evidence_index(index) == []


def test_filesystem_search_with_active_context_appends_line_evidence(tmp_path: Path):
    (tmp_path / "README.md").write_text("train here\nskip\ntrain again\n", encoding="utf-8")
    index = tmp_path / "evidence_index.jsonl"

    result = filesystem_search_with_evidence(
        search_request(tmp_path, "README.md"),
        permission_engine=allow_fs_engine(),
        evidence_index_path=index,
        evidence_id_prefix="ev_search",
    )

    assert result.tool_result.status == "success"
    assert [ref.evidence_id for ref in result.evidence] == ["ev_search_001", "ev_search_002"]
    assert [ref.start_line for ref in result.evidence] == [1, 3]
    assert len(read_evidence_index(index)) == 2
