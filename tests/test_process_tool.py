"""Tests for argv-based ProcessTool."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.tools import (
    PermissionEngine,
    PermissionProfile,
    ProcessToolRequest,
    run_process_tool,
)


def allow_process_engine() -> PermissionEngine:
    return PermissionEngine(
        profiles={
            "process_test": PermissionProfile(
                name="process_test",
                allow_tools={"process"},
            )
        }
    )


def request(tmp_path: Path, argv: list[str], *, timeout_seconds: int = 5) -> ProcessToolRequest:
    return ProcessToolRequest(
        tool_call_id="tool_001",
        argv=argv,
        cwd=tmp_path,
        cwd_label="workspace/repos/source_001",
        timeout_seconds=timeout_seconds,
        stage="tool_test",
        permission_profile="process_test",
        active_source_id="source_001",
    )


def test_process_tool_runs_argv_and_captures_output(tmp_path: Path):
    result = run_process_tool(
        request(tmp_path, [sys.executable, "-c", "print('ok')"]),
        permission_engine=allow_process_engine(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.stdout.strip() == "ok"
    assert result.output.exit_code == 0
    assert result.permission.permission_decision == "allow"


def test_process_tool_reports_nonzero_exit(tmp_path: Path):
    result = run_process_tool(
        request(tmp_path, [sys.executable, "-c", "import sys; sys.exit(7)"]),
        permission_engine=allow_process_engine(),
    )

    assert result.status == "failed"
    assert result.output is not None
    assert result.output.exit_code == 7


def test_process_tool_blocks_when_permission_denied(tmp_path: Path):
    engine = PermissionEngine(
        profiles={
            "process_test": PermissionProfile(
                name="process_test",
                deny_tools={"process"},
            )
        }
    )

    result = run_process_tool(
        request(tmp_path, [sys.executable, "-c", "print('should not run')"]),
        permission_engine=engine,
    )

    assert result.status == "blocked"
    assert result.output is None
    assert result.permission.permission_decision == "deny"


def test_process_tool_timeout(tmp_path: Path):
    result = run_process_tool(
        request(tmp_path, [sys.executable, "-c", "import time; time.sleep(2)"], timeout_seconds=1),
        permission_engine=allow_process_engine(),
    )

    assert result.status == "timed_out"
    assert result.output is not None
    assert result.output.timed_out is True


def test_process_request_rejects_shell_string_and_nul(tmp_path: Path):
    with pytest.raises(ValidationError):
        ProcessToolRequest(
            tool_call_id="tool_001",
            argv="echo unsafe",  # type: ignore[arg-type]
            cwd=tmp_path,
            cwd_label="workspace/repos/source_001",
            timeout_seconds=5,
            stage="analysis",
            permission_profile="repository_analysis",
        )

    with pytest.raises(ValidationError, match="NUL"):
        request(tmp_path, [sys.executable, "-c", "print('bad')", "bad\x00arg"])
