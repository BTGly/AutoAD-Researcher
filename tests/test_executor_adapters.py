from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter, ExecutorAdapterInputs

@pytest.mark.parametrize("adapter_id", ["generic_python", "patchcore_style", "anomalib_style"])
def test_explicit_adapter_fixture_builds_existing_runner_contract(tmp_path: Path, adapter_id: str):
    (tmp_path / "run.py").write_text("", encoding="utf-8"); (tmp_path / "evaluate.py").write_text("", encoding="utf-8")
    (tmp_path / "autoad_executor_adapter.json").write_text(json.dumps({"adapter_id":adapter_id,"entrypoint":"run.py","smoke_argv":[sys.executable,"run.py"],"metrics_output":"metrics.json","allowed_paths":["run.py"],"protected_paths":["evaluate.py"],"activation_evidence":"unverified"}), encoding="utf-8")
    result = ExecutorAdapter().inspect(tmp_path)
    assert result.status == "supported" and result.evidence is not None
    plan, refs = ExecutorAdapter().build_execution(result, ExecutorAdapterInputs(run_id="run_executor", worktree_ref="executor_worktrees/attempt", repository_fingerprint="fixture", environment_sha256="a"*64, dataset_manifest_sha256="b"*64, asset_manifest_sha256="c"*64))
    assert plan.program == sys.executable and plan.expected_outputs == ["metrics.json"] and refs.command_sha256

def test_adapter_does_not_guess_missing_or_invalid_evidence(tmp_path: Path):
    blocked = ExecutorAdapter().inspect(tmp_path)
    assert blocked.status == "blocked" and blocked.blocker
    (tmp_path / "autoad_executor_adapter.json").write_text("{}", encoding="utf-8")
    assert ExecutorAdapter().inspect(tmp_path).status == "blocked"


def test_b_test_requires_a_repository_declared_command(tmp_path: Path):
    (tmp_path / "run.py").write_text("", encoding="utf-8")
    (tmp_path / "evaluate.py").write_text("", encoding="utf-8")
    (tmp_path / "autoad_executor_adapter.json").write_text(
        json.dumps(
            {
                "adapter_id": "generic_python",
                "entrypoint": "run.py",
                "smoke_argv": [sys.executable, "run.py"],
                "metrics_output": "metrics.json",
                "allowed_paths": ["run.py"],
                "protected_paths": ["evaluate.py"],
                "evaluation_commands": {
                    "b_dev": {"args": ["run.py", "--split-ref", ""], "metrics_output": "metrics.json", "split_ref_arg_index": 2},
                    "b_test": {"args": ["run.py", "--split-ref", ""], "metrics_output": "metrics.json", "split_ref_arg_index": 2},
                },
            }
        ),
        encoding="utf-8",
    )
    result = ExecutorAdapter().inspect(tmp_path)
    assert result.status == "supported"
    plan, _ = ExecutorAdapter().build_execution(
        result,
        ExecutorAdapterInputs(
            run_id="run_executor",
            worktree_ref="executor_worktrees/attempt",
            repository_fingerprint="fixture",
            environment_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64,
            asset_manifest_sha256="c" * 64,
            evaluation_phase="b_test",
            split_ref="/run/inputs/test.json",
        ),
    )
    assert plan.args == ["run.py", "--split-ref", "/run/inputs/test.json"]
    assert plan.command_id == "generic_python_b_test"


@pytest.mark.parametrize("phase", ["b_dev", "b_test"])
def test_explicit_phase_command_can_bind_split_without_a_file_slot(tmp_path: Path, phase: str):
    (tmp_path / "run.py").write_text("", encoding="utf-8")
    (tmp_path / "evaluate.py").write_text("", encoding="utf-8")
    (tmp_path / "autoad_executor_adapter.json").write_text(
        json.dumps({
            "adapter_id": "generic_python",
            "entrypoint": "run.py",
            "smoke_argv": [sys.executable, "run.py"],
            "metrics_output": "metrics.json",
            "allowed_paths": ["run.py"],
            "protected_paths": ["evaluate.py"],
            "evaluation_commands": {
                "b_dev": {"args": ["run.py", "--split", "b_dev"], "metrics_output": "metrics.json"},
                "b_test": {"args": ["run.py", "--split", "b_test"], "metrics_output": "metrics.json"},
            },
        }),
        encoding="utf-8",
    )
    result = ExecutorAdapter().inspect(tmp_path)
    plan, _ = ExecutorAdapter().build_execution(
        result,
        ExecutorAdapterInputs(
            run_id="run_executor",
            worktree_ref="executor_worktrees/attempt",
            repository_fingerprint="fixture",
            environment_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64,
            asset_manifest_sha256="c" * 64,
            evaluation_phase=phase,
            split_ref=f"/run/inputs/{phase}.json",
        ),
    )
    assert plan.args == ["run.py", "--split", phase]


@pytest.mark.parametrize("phase", ["b_dev", "b_test"])
def test_indexed_phase_literal_is_not_replaced_by_split_path(tmp_path: Path, phase: str):
    (tmp_path / "run.py").write_text("", encoding="utf-8")
    (tmp_path / "evaluate.py").write_text("", encoding="utf-8")
    (tmp_path / "autoad_executor_adapter.json").write_text(
        json.dumps({
            "adapter_id": "generic_python",
            "entrypoint": "run.py",
            "smoke_argv": [sys.executable, "run.py"],
            "metrics_output": "metrics.json",
            "allowed_paths": ["run.py"],
            "protected_paths": ["evaluate.py"],
            "evaluation_commands": {
                "b_dev": {"args": ["run.py", "--split", "b_dev"], "metrics_output": "metrics.json", "split_ref_arg_index": 2},
                "b_test": {"args": ["run.py", "--split", "b_test"], "metrics_output": "metrics.json", "split_ref_arg_index": 2},
            },
        }),
        encoding="utf-8",
    )
    result = ExecutorAdapter().inspect(tmp_path)
    plan, _ = ExecutorAdapter().build_execution(
        result,
        ExecutorAdapterInputs(
            run_id="run_executor",
            worktree_ref="executor_worktrees/attempt",
            repository_fingerprint="fixture",
            environment_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64,
            asset_manifest_sha256="c" * 64,
            evaluation_phase=phase,
            split_ref=f"/run/inputs/{phase}.json",
        ),
    )
    assert plan.args == ["run.py", "--split", phase]


def test_baseline_split_binding_requires_an_explicit_manifest_slot(tmp_path: Path):
    (tmp_path / "run.py").write_text("", encoding="utf-8")
    (tmp_path / "evaluate.py").write_text("", encoding="utf-8")
    (tmp_path / "autoad_executor_adapter.json").write_text(
        json.dumps({
            "adapter_id": "generic_python",
            "entrypoint": "run.py",
            "smoke_argv": [sys.executable, "run.py"],
            "metrics_output": "metrics.json",
            "allowed_paths": ["run.py"],
            "protected_paths": ["evaluate.py"],
            "evaluation_commands": {"b_dev": {"args": ["run.py"], "metrics_output": "metrics.json"}},
        }),
        encoding="utf-8",
    )
    result = ExecutorAdapter().inspect(tmp_path)
    with pytest.raises(ValueError, match="split reference argument or explicit phase identity"):
        ExecutorAdapter().build_execution(
            result,
            ExecutorAdapterInputs(
                run_id="run_executor",
                worktree_ref="executor_worktrees/attempt",
                repository_fingerprint="fixture",
                environment_sha256="a" * 64,
                dataset_manifest_sha256="b" * 64,
                asset_manifest_sha256="c" * 64,
                evaluation_phase="b_dev",
                split_ref="/run/inputs/dev.json",
            ),
        )
