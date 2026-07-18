"""EX-B acceptance: unique edits, hard path guards, and rollback."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.experiment.patch_protocol import SearchReplaceApplier, SearchReplaceEdit, parse_search_replace_blocks
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


def _contract() -> InterventionContract:
    return InterventionContract(idea_id="idea_000001", mechanism="parameter adjustment", hypothesis="h", target_modules=["train.py"], allowed_paths=["train.py"], forbidden_paths=["evaluate.py"], allowed_parameters=["learning_rate"], evaluation_invariants=["fixed evaluator"], max_repairs=3, time_budget=60)


def _workspace(fixture_repository: Path, tmp_path: Path):
    return WorktreeManager(tmp_path / "worktrees").create(repository_path=fixture_repository, attempt_id="attempt_000001", base_commit="HEAD", protected_paths=["evaluate.py"], environment_snapshot_ref="environment/snapshot.json")


def test_three_search_replace_strategies_and_idempotency(fixture_repository: Path, tmp_path: Path):
    workspace = _workspace(fixture_repository, tmp_path)
    root = Path(workspace.worktree_path)
    applier = SearchReplaceApplier(contract=_contract(), workspace=workspace)
    perfect = SearchReplaceEdit(path="train.py", search="learning_rate = 0.1\n", replace="learning_rate = 0.2\n")
    result = applier.apply(perfect, diff_path=tmp_path / "patch.diff")
    assert result.status == "applied" and result.strategy == "perfect_replace"
    assert applier.apply(perfect).status == "unchanged"

    (root / "train.py").write_text("    value = 1\n", encoding="utf-8")
    indented = SearchReplaceEdit(path="train.py", search="value = 1\n", replace="value = 2\n")
    assert applier.apply(indented).strategy == "missing_leading_whitespace"

    (root / "train.py").write_text("before\nold\nafter\n", encoding="utf-8")
    dots = SearchReplaceEdit(path="train.py", search="before\n...\nafter\n", replace="before\nnew\nafter\n")
    dots_result = applier.apply(dots)
    assert dots_result.status == "applied" and dots_result.strategy == "try_dotdotdots", dots_result
    assert (root / "train.py").read_text(encoding="utf-8") == "before\nnew\nafter\n"


def test_protocol_rejects_ambiguous_forbidden_and_protected_edits(fixture_repository: Path, tmp_path: Path):
    workspace = _workspace(fixture_repository, tmp_path)
    root = Path(workspace.worktree_path)
    (root / "train.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    applier = SearchReplaceApplier(contract=_contract(), workspace=workspace)
    assert applier.apply(SearchReplaceEdit(path="train.py", search="x = 1\n", replace="x = 2\n")).decision.code == "SEARCH_NOT_UNIQUE"
    forbidden = applier.apply(SearchReplaceEdit(path="evaluate.py", search="protected = True\n", replace="protected = False\n"))
    assert forbidden.status == "rejected" and forbidden.decision.code == "REPAIR_REJECTED_HARD"
    assert (root / "evaluate.py").read_text(encoding="utf-8") == "protected = True\n"


def test_syntax_failure_rolls_back_and_parser_is_strict(fixture_repository: Path, tmp_path: Path):
    workspace = _workspace(fixture_repository, tmp_path)
    root = Path(workspace.worktree_path)
    applier = SearchReplaceApplier(contract=_contract(), workspace=workspace)
    failed = applier.apply(SearchReplaceEdit(path="train.py", search="learning_rate = 0.1\n", replace="def broken(:\n"))
    assert failed.status == "rolled_back"
    assert (root / "train.py").read_text(encoding="utf-8") == "learning_rate = 0.1\n"
    parsed = parse_search_replace_blocks("<<<<<<< SEARCH\nlearning_rate = 0.1\n=======\nlearning_rate = 0.2\n>>>>>>> REPLACE\n", path="train.py")
    assert parsed[0].path == "train.py"
