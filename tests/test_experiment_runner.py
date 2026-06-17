"""Tests for generic controlled experiment runner."""

import sys
from pathlib import Path

import pytest

from autoad_researcher.environments import CommandExecutionOutput, ResolvedCommand
from autoad_researcher.runner import (
    ExperimentCommandPlan,
    ExperimentInputRefs,
    execute_experiment_attempt,
    experiment_command_sha256,
    run_experiment_subprocess,
)


def command_plan(**overrides) -> ExperimentCommandPlan:
    data = {
        "schema_version": 1,
        "command_id": "attempt_command",
        "program": "python",
        "args": ["train.py", "--epochs", "1"],
        "cwd": "workspace/repos/project",
        "environment": {},
        "timeout_seconds": 30,
        "network": False,
        "expected_outputs": ["metrics.json"],
    }
    data.update(overrides)
    return ExperimentCommandPlan.model_validate(data)


def input_refs(plan: ExperimentCommandPlan, **overrides) -> ExperimentInputRefs:
    data = {
        "repository_fingerprint": "repo-clean",
        "environment_sha256": "a" * 64,
        "dataset_manifest_sha256": "b" * 64,
        "asset_manifest_sha256": "c" * 64,
        "command_sha256": experiment_command_sha256(plan),
    }
    data.update(overrides)
    return ExperimentInputRefs.model_validate(data)


def test_command_plan_rejects_network_true():
    data = command_plan().model_dump(mode="json")
    data["network"] = True

    with pytest.raises(Exception):
        ExperimentCommandPlan.model_validate(data)


def test_command_plan_rejects_shell_metacharacter():
    with pytest.raises(ValueError, match="shell"):
        command_plan(args=["train.py", "|", "tee", "log"])


def test_output_dir_must_not_exist(tmp_path: Path):
    plan = command_plan()
    attempt_dir = tmp_path / "attempt_01"
    attempt_dir.mkdir()

    with pytest.raises(FileExistsError):
        execute_experiment_attempt(
            run_id="run_demo",
            attempt="attempt_01",
            command_plan=plan,
            input_refs=input_refs(plan),
            attempt_dir=attempt_dir,
            runner=lambda *_: CommandExecutionOutput(exit_code=0),
        )


def test_command_hash_mismatch_preflight_failed(tmp_path: Path):
    plan = command_plan()

    result = execute_experiment_attempt(
        run_id="run_demo",
        attempt="attempt_01",
        command_plan=plan,
        input_refs=input_refs(plan, command_sha256="0" * 64),
        attempt_dir=tmp_path / "attempt_01",
        runner=lambda *_: CommandExecutionOutput(exit_code=0),
    )

    assert result.status == "preflight_failed"
    assert result.failure_code == "RUN_COMMAND_HASH_MISMATCH"


def test_success_writes_outputs_and_result(tmp_path: Path):
    plan = command_plan()

    def runner(command: ResolvedCommand, attempt_dir: Path) -> CommandExecutionOutput:
        assert command.argv == ["python", "train.py", "--epochs", "1"]
        (attempt_dir / "metrics.json").write_text('{"auroc": 0.9}', encoding="utf-8")
        return CommandExecutionOutput(exit_code=0, stdout="ok", stderr="")

    result = execute_experiment_attempt(
        run_id="run_demo",
        attempt="attempt_01",
        command_plan=plan,
        input_refs=input_refs(plan),
        attempt_dir=tmp_path / "attempt_01",
        runner=runner,
    )

    assert result.status == "success"
    assert (tmp_path / "attempt_01/output_manifest.json").is_file()
    assert (tmp_path / "attempt_01/execution_result.json").is_file()
    assert (tmp_path / "attempt_01/stdout.log").read_text(encoding="utf-8") == "ok"


def test_nonzero_exit_is_execution_failed(tmp_path: Path):
    plan = command_plan()

    result = execute_experiment_attempt(
        run_id="run_demo",
        attempt="attempt_01",
        command_plan=plan,
        input_refs=input_refs(plan),
        attempt_dir=tmp_path / "attempt_01",
        runner=lambda *_: CommandExecutionOutput(exit_code=2, stdout="", stderr="bad"),
    )

    assert result.status == "execution_failed"
    assert result.failure_code == "RUN_COMMAND_FAILED"


def test_timeout_is_execution_failed(tmp_path: Path):
    plan = command_plan()

    result = execute_experiment_attempt(
        run_id="run_demo",
        attempt="attempt_01",
        command_plan=plan,
        input_refs=input_refs(plan),
        attempt_dir=tmp_path / "attempt_01",
        runner=lambda *_: CommandExecutionOutput(exit_code=None, timed_out=True),
    )

    assert result.status == "execution_failed"
    assert result.failure_code == "RUN_TIMEOUT"
    assert result.timed_out is True


def test_missing_expected_output_fails(tmp_path: Path):
    plan = command_plan()

    result = execute_experiment_attempt(
        run_id="run_demo",
        attempt="attempt_01",
        command_plan=plan,
        input_refs=input_refs(plan),
        attempt_dir=tmp_path / "attempt_01",
        runner=lambda *_: CommandExecutionOutput(exit_code=0),
    )

    assert result.status == "execution_failed"
    assert result.failure_code == "RUN_EXPECTED_OUTPUT_MISSING"


def test_repository_mutation_invalidates_attempt(tmp_path: Path):
    plan = command_plan()

    def runner(command: ResolvedCommand, attempt_dir: Path) -> CommandExecutionOutput:
        (attempt_dir / "metrics.json").write_text("{}", encoding="utf-8")
        return CommandExecutionOutput(exit_code=0)

    result = execute_experiment_attempt(
        run_id="run_demo",
        attempt="attempt_01",
        command_plan=plan,
        input_refs=input_refs(plan),
        attempt_dir=tmp_path / "attempt_01",
        runner=runner,
        repository_fingerprint_after="dirty",
    )

    assert result.status == "invalid_repository_mutation"
    assert result.failure_code == "RUN_REPOSITORY_MUTATED"


def test_repository_fingerprint_probe_runs_after_runner(tmp_path: Path):
    plan = command_plan()
    observed = {"runner_finished": False}

    def runner(command: ResolvedCommand, attempt_dir: Path) -> CommandExecutionOutput:
        (attempt_dir / "metrics.json").write_text("{}", encoding="utf-8")
        observed["runner_finished"] = True
        return CommandExecutionOutput(exit_code=0)

    def after_probe() -> str:
        assert observed["runner_finished"] is True
        return "repo-clean"

    result = execute_experiment_attempt(
        run_id="run_demo",
        attempt="attempt_01",
        command_plan=plan,
        input_refs=input_refs(plan),
        attempt_dir=tmp_path / "attempt_01",
        runner=runner,
        repository_fingerprint_after=after_probe,
    )

    assert result.status == "success"


def test_subprocess_runner_executes_shell_false_attempt(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "write_metrics.py").write_text(
        "from pathlib import Path\nPath('metrics.json').write_text('{}')\n",
        encoding="utf-8",
    )
    plan = command_plan(
        program=sys.executable,
        args=["../write_metrics.py"],
        cwd="attempt_01",
    )

    result = execute_experiment_attempt(
        run_id="run_demo",
        attempt="attempt_01",
        command_plan=plan,
        input_refs=input_refs(plan),
        attempt_dir=tmp_path / "attempt_01",
        runner=run_experiment_subprocess,
    )

    assert result.status == "success"
    assert (tmp_path / "attempt_01/metrics.json").is_file()
