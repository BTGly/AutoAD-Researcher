"""测试 repository preflight."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoad_researcher.benchmarks.errors import BenchmarkPreflightError
from autoad_researcher.benchmarks.repository import collect_repository_state, verify_repository_unchanged


def _make_git_repo(repo: Path, *, commit_msg="init"):
    """Initialize a real git repo with one commit on detached HEAD."""
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "README.md").write_text(commit_msg)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo, check=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                            capture_output=True, text=True, check=True).stdout.strip()
    # Detach HEAD
    subprocess.run(["git", "checkout", "--detach", commit], cwd=repo, check=True, capture_output=True)
    return commit


def _mock_case(commit, *, origin_url="https://github.com/test/repo", entrypoint="README.md"):
    return SimpleNamespace(
        case_id="test",
        repository=SimpleNamespace(
            url=origin_url, commit_sha=commit,
            entrypoint_path=entrypoint, config_path=None, dependency_files=[],
        ),
        evaluation=SimpleNamespace(evaluator_paths=[], protected_paths=[]),
        dataset=SimpleNamespace(root_env="FOO", name="x", category="y"),
    )


class TestRepositoryPreflight:
    def test_valid_repo_passes(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "repos").mkdir(parents=True)
        repo = ws / "repos" / "test-repo"
        repo.mkdir()
        commit = _make_git_repo(repo)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/test/repo"], cwd=repo, check=True)

        case = _mock_case(commit)
        state = collect_repository_state(case=case, repo_path=repo, workspace_root=ws)
        assert state.actual_commit == commit
        assert state.detached_head is True
        assert state.dirty is False

    def test_non_git_rejected(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "repos").mkdir(parents=True)
        not_git = ws / "repos" / "not-git"
        not_git.mkdir()

        case = _mock_case("a" * 40)
        with pytest.raises(BenchmarkPreflightError, match="not a git repository"):
            collect_repository_state(case=case, repo_path=not_git, workspace_root=ws)

    def test_path_outside_workspace_rejected(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "repos").mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()

        case = _mock_case("a" * 40)
        with pytest.raises(BenchmarkPreflightError, match="repository must be inside"):
            collect_repository_state(case=case, repo_path=outside, workspace_root=ws)

    def test_dirty_repo_rejected(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "repos").mkdir(parents=True)
        repo = ws / "repos" / "test-repo"
        repo.mkdir()
        commit = _make_git_repo(repo)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/test/repo"], cwd=repo, check=True)
        (repo / "dirty.txt").write_text("changed")

        case = _mock_case(commit)
        with pytest.raises(BenchmarkPreflightError, match="uncommitted"):
            collect_repository_state(case=case, repo_path=repo, workspace_root=ws)

    def test_wrong_commit_rejected(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "repos").mkdir(parents=True)
        repo = ws / "repos" / "test-repo"
        repo.mkdir()
        _make_git_repo(repo)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/test/repo"], cwd=repo, check=True)

        case = _mock_case("b" * 40)
        with pytest.raises(BenchmarkPreflightError, match="expected"):
            collect_repository_state(case=case, repo_path=repo, workspace_root=ws)

    def test_attached_branch_rejected(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "repos").mkdir(parents=True)
        repo = ws / "repos" / "test-repo"
        repo.mkdir()
        commit = _make_git_repo(repo)
        # Re-attach to a branch
        subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/test/repo"], cwd=repo, check=True)

        case = _mock_case(commit)
        with pytest.raises(BenchmarkPreflightError, match="detached"):
            collect_repository_state(case=case, repo_path=repo, workspace_root=ws)

    def test_fingerprint_stable(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "repos").mkdir(parents=True)
        repo = ws / "repos" / "test-repo"
        repo.mkdir()
        commit = _make_git_repo(repo)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/test/repo"], cwd=repo, check=True)

        case = _mock_case(commit)
        s1 = collect_repository_state(case=case, repo_path=repo, workspace_root=ws)
        s2 = collect_repository_state(case=case, repo_path=repo, workspace_root=ws)
        assert s1.repository_fingerprint == s2.repository_fingerprint

    def test_verify_unchanged_passes(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "repos").mkdir(parents=True)
        repo = ws / "repos" / "test-repo"
        repo.mkdir()
        commit = _make_git_repo(repo)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/test/repo"], cwd=repo, check=True)

        case = _mock_case(commit)
        s1 = collect_repository_state(case=case, repo_path=repo, workspace_root=ws)
        s2 = collect_repository_state(case=case, repo_path=repo, workspace_root=ws)
        verify_repository_unchanged(before=s1, after=s2)  # should not raise

    def test_verify_mutation_detected(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "repos").mkdir(parents=True)
        repo = ws / "repos" / "test-repo"
        repo.mkdir()
        commit = _make_git_repo(repo)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/test/repo"], cwd=repo, check=True)

        case = _mock_case(commit)
        collect_repository_state(case=case, repo_path=repo, workspace_root=ws)
        (repo / "README.md").write_text("mutated content")
        with pytest.raises(BenchmarkPreflightError, match="uncommitted or untracked"):
            collect_repository_state(case=case, repo_path=repo, workspace_root=ws)

    def test_missing_required_file_rejected(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "repos").mkdir(parents=True)
        repo = ws / "repos" / "test-repo"
        repo.mkdir()
        commit = _make_git_repo(repo)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/test/repo"], cwd=repo, check=True)

        case = _mock_case(commit, entrypoint="missing.py")
        with pytest.raises(BenchmarkPreflightError, match="required file missing"):
            collect_repository_state(case=case, repo_path=repo, workspace_root=ws)
