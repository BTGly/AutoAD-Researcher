"""Tests for NarrowRepositoryReader contract enforcement."""

import tempfile
from pathlib import Path

import pytest

from autoad_researcher.code_agent.narrow_repo_read import NarrowRepositoryReader
from autoad_researcher.schemas.patch_planning import NarrowRepositoryReadRequest


@pytest.fixture
def repo(tmp_path):
    """Create a repo with a few test files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main(): pass\n")
    (tmp_path / "src" / "utils.py").write_text("def util(): pass\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("# readme\n")
    (tmp_path / "setup.py").write_text("from setuptools import setup\n")
    return tmp_path


def test_read_file_within_allowed_paths(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=["src/"],
    )
    reader = NarrowRepositoryReader(req, repo)
    data = reader.read_file("src/main.py")
    assert data == b"def main(): pass\n"


def test_read_file_outside_allowed_paths(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=["src/"],
    )
    reader = NarrowRepositoryReader(req, repo)
    with pytest.raises(PermissionError, match="not in allowed_paths"):
        reader.read_file("setup.py")


def test_read_file_traversal(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=[],
    )
    reader = NarrowRepositoryReader(req, repo)
    with pytest.raises(PermissionError, match="path traversal denied"):
        reader.read_file("../../etc/passwd")


def test_max_files_limit(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=[],
        max_files=2,
    )
    reader = NarrowRepositoryReader(req, repo)
    reader.read_file("src/main.py")
    reader.read_file("src/utils.py")
    with pytest.raises(PermissionError, match="max_files limit"):
        reader.read_file("setup.py")


def test_max_bytes_limit(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=[],
        max_bytes=5,
    )
    reader = NarrowRepositoryReader(req, repo)
    with pytest.raises(PermissionError, match="max_bytes limit"):
        reader.read_file("src/main.py")


def test_read_source_files_respects_allowed_paths(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=["src/"],
    )
    reader = NarrowRepositoryReader(req, repo)
    files = reader.read_source_files(extensions={".py"})
    paths = [rel for rel, _ in files]
    assert "src/main.py" in paths
    assert "src/utils.py" in paths
    assert "setup.py" not in paths


def test_read_source_files_respects_extensions(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=[],
    )
    reader = NarrowRepositoryReader(req, repo)
    files = reader.read_source_files(extensions={".md"})
    paths = [rel for rel, _ in files]
    assert "docs/readme.md" in paths
    assert "src/main.py" not in paths


def test_read_source_files_respects_max_bytes(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=[],
        max_bytes=10,
    )
    reader = NarrowRepositoryReader(req, repo)
    files = reader.read_source_files(extensions={".py"})
    total = sum(len(c) for _, c in files)
    assert total <= 10


def test_list_files_respects_allowed_paths(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=["src/"],
    )
    reader = NarrowRepositoryReader(req, repo)
    files = reader.list_files()
    assert "src/main.py" in files
    assert "setup.py" not in files


def test_list_files_respects_max_files(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=[],
        max_files=2,
    )
    reader = NarrowRepositoryReader(req, repo)
    files = reader.list_files()
    assert len(files) <= 2


def test_empty_allowed_paths_reads_all(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=[],
    )
    reader = NarrowRepositoryReader(req, repo)
    data = reader.read_file("setup.py")
    assert data == b"from setuptools import setup\n"


def test_path_prefix_matching(repo):
    (repo / "src-staging" / "x.py").parent.mkdir(parents=True, exist_ok=True)
    (repo / "src-staging" / "x.py").write_text("x = 1\n")
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=["src/"],
    )
    reader = NarrowRepositoryReader(req, repo)
    with pytest.raises(PermissionError, match="not in allowed_paths"):
        reader.read_file("src-staging/x.py")


def test_requested_paths_alone_does_not_restrict(repo):
    req = NarrowRepositoryReadRequest(
        repository_source_id="src_test", repository_commit="a" * 40,
        allowed_paths=[],
        requested_paths=["src/main.py"],
    )
    reader = NarrowRepositoryReader(req, repo)
    data = reader.read_file("setup.py")
    assert data == b"from setuptools import setup\n"
