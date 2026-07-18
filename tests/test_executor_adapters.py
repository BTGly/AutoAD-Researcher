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
