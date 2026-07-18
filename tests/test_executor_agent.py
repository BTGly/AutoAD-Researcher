"""EX-C acceptance: the temporary Executor has narrow, auditable bounds."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autoad_researcher.experiment.executor_agent import ExecutorAgent, ExecutorLimits, ExecutorProposal, ExecutorTools
from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.experiment.patch_protocol import SearchReplaceEdit
from autoad_researcher.experiment.worktree import WorktreeManager


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, check=True, text=True, capture_output=True, shell=False).stdout.strip()


@pytest.fixture
def fixture_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "baseline"; repository.mkdir()
    _git(repository, "init", "-b", "main"); _git(repository, "config", "user.email", "fixture@example.invalid"); _git(repository, "config", "user.name", "fixture")
    (repository / "train.py").write_text("learning_rate = 0.1\n", encoding="utf-8"); (repository / "evaluate.py").write_text("protected = True\n", encoding="utf-8")
    _git(repository, "add", "train.py", "evaluate.py"); _git(repository, "commit", "-m", "fixture baseline")
    return repository


def _contract() -> InterventionContract:
    return InterventionContract(idea_id="idea_000001", mechanism="parameter adjustment", hypothesis="h", target_modules=["train.py"], allowed_paths=["train.py"], forbidden_paths=["evaluate.py"], allowed_parameters=["learning_rate"], evaluation_invariants=["fixed evaluator"], max_repairs=3, time_budget=60)


def _agent(fixture_repository: Path, tmp_path: Path, limits: ExecutorLimits):
    workspace = WorktreeManager(tmp_path / "worktrees").create(repository_path=fixture_repository, attempt_id="attempt_000001", base_commit="HEAD", protected_paths=["evaluate.py"], environment_snapshot_ref="environment/snapshot.json")
    return ExecutorAgent(contract=_contract(), workspace=workspace, artifact_dir=tmp_path / "attempts" / "attempt_000001", limits=limits), workspace


def test_executor_applies_only_structured_edit_and_always_writes_summary(fixture_repository: Path, tmp_path: Path):
    agent, workspace = _agent(fixture_repository, tmp_path, ExecutorLimits(max_steps=4, max_wall_seconds=30, max_model_calls=1))
    summary = agent.run(lambda _tools: ExecutorProposal(edits=[SearchReplaceEdit(path="train.py", search="learning_rate = 0.1\n", replace="learning_rate = 0.2\n")], changed_symbols=["learning_rate"], possible_contract_deviation="entrypoint use was not independently established", confidence=.6))
    assert summary.status == "completed"
    saved = json.loads((tmp_path / "attempts" / "attempt_000001" / "executor_summary.json").read_text(encoding="utf-8"))
    assert saved["possible_contract_deviation"] == "entrypoint use was not independently established"
    assert (Path(workspace.worktree_path) / "train.py").read_text(encoding="utf-8") == "learning_rate = 0.2\n"


def test_executor_rejects_empty_edit_proposal(fixture_repository: Path, tmp_path: Path):
    agent, _ = _agent(fixture_repository, tmp_path, ExecutorLimits(max_steps=4, max_wall_seconds=30, max_model_calls=1))
    summary = agent.run(lambda _tools: ExecutorProposal(edits=[], changed_symbols=[], confidence=.5))
    assert summary.status == "implementation_failed"
    assert summary.error == "proposal did not include edits"


def test_executor_budget_and_command_allowlist_are_hard_bounds(fixture_repository: Path, tmp_path: Path):
    agent, workspace = _agent(fixture_repository, tmp_path, ExecutorLimits(max_steps=1, max_wall_seconds=30, max_model_calls=0))
    exhausted = agent.run(lambda _tools: pytest.fail("provider must not be called"))
    assert exhausted.status == "budget_exhausted"
    assert (tmp_path / "attempts" / "attempt_000001" / "executor_summary.json").is_file()

    from autoad_researcher.experiment.patch_protocol import SearchReplaceApplier
    tools = ExecutorTools(worktree_path=Path(workspace.worktree_path), applier=SearchReplaceApplier(contract=_contract(), workspace=workspace), limits=ExecutorLimits(max_steps=2, max_wall_seconds=30, max_model_calls=1))
    with pytest.raises(PermissionError):
        tools.run_command(["git", "status"], timeout_seconds=1)


def test_executor_allows_one_bounded_repair_after_initial_syntax_failure(fixture_repository: Path, tmp_path: Path):
    contract = _contract().model_copy(update={"max_repairs": 1})
    workspace = WorktreeManager(tmp_path / "worktrees").create(repository_path=fixture_repository, attempt_id="attempt_000001", base_commit="HEAD", protected_paths=["evaluate.py"], environment_snapshot_ref="environment/snapshot.json")
    agent = ExecutorAgent(contract=contract, workspace=workspace, artifact_dir=tmp_path / "attempts" / "attempt_000001", limits=ExecutorLimits(max_steps=4, max_wall_seconds=30, max_model_calls=2))
    proposals = iter([
        ExecutorProposal(edits=[SearchReplaceEdit(path="train.py", search="learning_rate = 0.1\n", replace="def broken(:\n")], changed_symbols=[], confidence=.1),
        ExecutorProposal(edits=[SearchReplaceEdit(path="train.py", search="learning_rate = 0.1\n", replace="learning_rate = 0.2\n")], changed_symbols=["learning_rate"], confidence=.9),
    ])
    summary = agent.run(lambda _tools: next(proposals))
    assert summary.status == "completed" and summary.model_calls == 2
    records = (tmp_path / "attempts" / "attempt_000001" / "repair_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == 1


def test_repeated_hard_violation_stops_and_preserves_protected_file(fixture_repository: Path, tmp_path: Path):
    agent, workspace = _agent(fixture_repository, tmp_path, ExecutorLimits(max_steps=4, max_wall_seconds=30, max_model_calls=4))
    proposal = lambda _tools: ExecutorProposal(edits=[SearchReplaceEdit(path="evaluate.py", search="protected = True\n", replace="protected = False\n")], changed_symbols=["protected"], possible_contract_deviation="requested evaluation change is outside the frozen contract", confidence=.2)
    summary = agent.run(proposal)
    assert summary.status == "implementation_failed" and summary.model_calls == 2
    assert summary.possible_contract_deviation
    assert (Path(workspace.worktree_path) / "evaluate.py").read_text(encoding="utf-8") == "protected = True\n"
    assert len((tmp_path / "attempts" / "attempt_000001" / "repair_log.jsonl").read_text(encoding="utf-8").splitlines()) == 2
