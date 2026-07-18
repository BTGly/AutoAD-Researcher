"""EX-A acceptance: frozen contracts and isolated git worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.experiment.executor_contracts import InterventionContract, freeze_protected_hashes
from autoad_researcher.experiment.worktree import WorktreeManager


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, check=True, text=True, capture_output=True, shell=False).stdout.strip()


@pytest.fixture
def fixture_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "baseline"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "fixture")
    (repository / "train.py").write_text("learning_rate = 0.1\n", encoding="utf-8")
    (repository / "evaluate.py").write_text("protected = True\n", encoding="utf-8")
    _git(repository, "add", "train.py", "evaluate.py")
    _git(repository, "commit", "-m", "fixture baseline")
    return repository


def test_contract_rejects_overlapping_or_escaping_paths():
    with pytest.raises(ValueError):
        InterventionContract(
            idea_id="idea_000001", mechanism="m", hypothesis="h", target_modules=["train.py"],
            allowed_paths=["train.py"], forbidden_paths=["train.py"], time_budget=60,
        )
    with pytest.raises(ValueError):
        InterventionContract(
            idea_id="idea_000001", mechanism="m", hypothesis="h", target_modules=["../train.py"],
            allowed_paths=["train.py"], time_budget=60,
        )


def test_each_attempt_gets_an_isolated_worktree_and_preserves_baseline(fixture_repository: Path, tmp_path: Path):
    manager = WorktreeManager(tmp_path / "executor_worktrees")
    base = _git(fixture_repository, "rev-parse", "HEAD")
    first = manager.create(repository_path=fixture_repository, attempt_id="attempt_000001", base_commit=base, protected_paths=["evaluate.py"], environment_snapshot_ref="environment/snapshot.json")
    second = manager.create(repository_path=fixture_repository, attempt_id="attempt_000002", base_commit=base, protected_paths=["evaluate.py"], environment_snapshot_ref="environment/snapshot.json")

    first_root = Path(first.worktree_path)
    second_root = Path(second.worktree_path)
    (first_root / "train.py").write_text("learning_rate = 0.2\n", encoding="utf-8")

    assert first_root != second_root
    assert (second_root / "train.py").read_text(encoding="utf-8") == "learning_rate = 0.1\n"
    assert (fixture_repository / "train.py").read_text(encoding="utf-8") == "learning_rate = 0.1\n"
    assert _git(fixture_repository, "status", "--porcelain") == ""
    assert first.protected_hashes == {"evaluate.py": sha256_file(fixture_repository / "evaluate.py")}
    assert freeze_protected_hashes(first_root, ["evaluate.py"]) == first.protected_hashes


def test_manager_rejects_dirty_source_and_keeps_artifacts_outside_cleanup(fixture_repository: Path, tmp_path: Path):
    manager = WorktreeManager(tmp_path / "executor_worktrees")
    (fixture_repository / "train.py").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean"):
        manager.create(repository_path=fixture_repository, attempt_id="attempt_000001", base_commit="HEAD", protected_paths=["evaluate.py"], environment_snapshot_ref="environment/snapshot.json")
    _git(fixture_repository, "checkout", "--", "train.py")
    workspace = manager.create(repository_path=fixture_repository, attempt_id="attempt_000001", base_commit="HEAD", protected_paths=["evaluate.py"], environment_snapshot_ref="environment/snapshot.json")
    artifact = tmp_path / "attempts" / "attempt_000001" / "workspace.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("preserved", encoding="utf-8")
    manager.remove(Path(workspace.worktree_path))
    assert artifact.read_text(encoding="utf-8") == "preserved"
