"""Tests for Repository Intelligence R6 acquisition and attestation."""

import subprocess
from pathlib import Path

from autoad_researcher.repository_intelligence import (
    RepositoryAcquisitionRequest,
    RepositoryAcquisitionRunner,
    read_evidence_index,
)
from autoad_researcher.repository_intelligence.acquisition import _validate_git_argv


def make_remote_repo(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    run(["git", "init", "-b", "main"], cwd=source)
    (source / "README.md").write_text("hello\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=source)
    run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "initial"], cwd=source)
    commit = run(["git", "rev-parse", "HEAD"], cwd=source).stdout.strip()
    return source, commit


def run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=True)


def test_shallow_ref_acquisition_attests_detached_clean_repository(tmp_path: Path):
    remote, commit = make_remote_repo(tmp_path)
    run_dir = tmp_path / "run"
    workspace = tmp_path / "workspace"

    result = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_001",
            workspace_root=workspace,
            remote_url=remote.as_posix(),
            resolved_ref="main",
            resolved_commit=commit,
            acquisition_profile="shallow_ref",
        ),
        run_dir=run_dir,
    )

    assert result.status == "success"
    assert result.source is not None
    assert result.attestation is not None
    assert result.source.resolved_commit == commit
    assert result.source.acquisition_profile == "shallow_ref"
    assert result.attestation.detached_head is True
    assert result.attestation.dirty is False
    assert (run_dir / "repository_source.json").is_file()
    assert (run_dir / "repository_attestation.json").is_file()
    assert (run_dir / "acquisition_tool_calls.jsonl").is_file()
    assert (run_dir / "acquisition_permission_decisions.jsonl").is_file()
    evidence = read_evidence_index(run_dir / "evidence_index.jsonl")
    assert evidence[0].evidence.source_kind == "repository_identity"
    assert evidence[0].evidence.source_id == "source_001"


def test_partial_exact_acquisition_does_not_use_depth(tmp_path: Path):
    remote, commit = make_remote_repo(tmp_path)
    run_dir = tmp_path / "run"
    workspace = tmp_path / "workspace"

    result = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_002",
            workspace_root=workspace,
            remote_url=remote.as_posix(),
            resolved_commit=commit,
            acquisition_profile="partial_exact",
        ),
        run_dir=run_dir,
    )

    assert result.status == "success"
    assert result.source is not None
    assert result.source.acquisition_profile == "partial_exact"
    clone_call = next(call for call in result.tool_calls if call.tool_call_id == "tool_git_clone")
    assert "--filter=blob:none" in clone_call.argv
    assert all(not arg.startswith("--depth") for arg in clone_call.argv)


def test_generic_shallow_acquisition_does_not_require_resolved_commit(tmp_path: Path):
    remote, commit = make_remote_repo(tmp_path)
    run_dir = tmp_path / "run"
    workspace = tmp_path / "workspace"

    result = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_generic",
            workspace_root=workspace,
            remote_url=remote.as_posix(),
            acquisition_profile="generic_shallow",
        ),
        run_dir=run_dir,
    )

    assert result.status == "success"
    assert result.source is not None
    assert result.source.acquisition_profile == "generic_shallow"
    assert result.source.resolved_commit == commit
    clone_call = next(call for call in result.tool_calls if call.tool_call_id == "tool_git_clone")
    assert "--depth=1" in clone_call.argv


def test_ref_move_is_structured_failure(tmp_path: Path):
    remote, _commit = make_remote_repo(tmp_path)
    run_dir = tmp_path / "run"
    workspace = tmp_path / "workspace"

    result = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_003",
            workspace_root=workspace,
            remote_url=remote.as_posix(),
            resolved_ref="main",
            resolved_commit="f" * 40,
            acquisition_profile="shallow_ref",
        ),
        run_dir=run_dir,
    )

    assert result.status == "failed"
    assert result.error_code == "ACQUISITION_FAILED"
    assert "SOURCE_REF_MOVED" in (result.error_message or "")


def test_git_filter_and_depth_mix_is_forbidden():
    try:
        _validate_git_argv(["git", "clone", "--filter=blob:none", "--depth=1", "origin", "target"])
    except Exception as exc:
        assert "--filter and --depth" in str(exc)
    else:
        raise AssertionError("mixed partial and shallow clone should be forbidden")


def test_credential_remote_url_is_rejected(tmp_path: Path):
    result = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_004",
            workspace_root=tmp_path / "workspace",
            remote_url="https://token@github.com/example/repo",
            resolved_ref="main",
            resolved_commit="a" * 40,
            acquisition_profile="shallow_ref",
        ),
        run_dir=tmp_path / "run",
    )

    assert result.status == "failed"
    assert "credential-bearing" in (result.error_message or "")


def test_local_non_git_source_gets_stable_tree_fingerprint(tmp_path: Path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "data.txt").write_text("x\n", encoding="utf-8")

    result = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_005",
            workspace_root=tmp_path / "workspace",
            local_path=local,
            acquisition_profile="local",
        ),
        run_dir=tmp_path / "run",
    )

    assert result.status == "success"
    assert result.source is not None
    assert result.source.kind == "local_workspace"
    assert result.source.resolved_commit is None
    assert result.attestation is not None
    assert result.attestation.detached_head is None
