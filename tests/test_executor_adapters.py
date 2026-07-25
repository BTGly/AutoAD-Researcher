from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter, ExecutorAdapterDraft, ExecutorAdapterInputs, STANDARD_PREFLIGHT_CHECKS
from autoad_researcher.experiment.preflight import ensure_adapter_preflight, run_adapter_preflight

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


def test_agent_adapter_draft_is_separate_and_requires_backend_preflight(tmp_path: Path):
    for name in ["train.py", "evaluate.py", "dataset.py", "checkpoint.py", "metrics.py"]:
        (tmp_path / name).write_text("print('ok')\n", encoding="utf-8")
    commands = {name: {"args": [sys.executable, "train.py"]} for name in STANDARD_PREFLIGHT_CHECKS}
    draft = ExecutorAdapterDraft(
        adapter_id="anomalyclip_mvtec",
        entrypoint="train.py",
        smoke_argv=[sys.executable, "train.py"],
        metrics_output="metrics.json",
        allowed_paths=["train.py"],
        protected_paths=["evaluate.py"],
        investigation_evidence_ids=["ev_cli"],
        source_files=["train.py", "dataset.py"],
        preflight_commands=commands,
    )
    inspected = ExecutorAdapter().inspect_draft(tmp_path, draft)
    assert inspected.status == "supported"
    assert inspected.source == "agent_draft"
    assert inspected.required_preflight_checks == STANDARD_PREFLIGHT_CHECKS
    result = run_adapter_preflight(tmp_path, inspected.evidence, required_checks=inspected.required_preflight_checks)
    assert result.status == "passed"


def test_agent_adapter_draft_fails_closed_when_a_standard_check_is_missing(tmp_path: Path):
    (tmp_path / "train.py").write_text("print('ok')\n", encoding="utf-8")
    draft = ExecutorAdapterDraft(
        adapter_id="faprompt_mvtecad",
        entrypoint="train.py",
        smoke_argv=[sys.executable, "train.py"],
        metrics_output="metrics.json",
        allowed_paths=["train.py"],
        protected_paths=["train.py"],
        investigation_evidence_ids=["ev_cli"],
        source_files=["train.py"],
        preflight_commands={"help": {"args": [sys.executable, "train.py"]}},
    )
    inspected = ExecutorAdapter().inspect_draft(tmp_path, draft)
    assert inspected.status == "blocked"
    assert "mandatory preflight checks" in (inspected.blocker or "")


def test_agent_adapter_draft_rejects_unobserved_source_files(tmp_path: Path):
    (tmp_path / "train.py").write_text("print('ok')\n", encoding="utf-8")
    draft = ExecutorAdapterDraft(
        adapter_id="anomalyclip_mvtec",
        entrypoint="train.py",
        smoke_argv=[sys.executable, "train.py"],
        metrics_output="metrics.json",
        allowed_paths=["train.py"],
        protected_paths=["train.py"],
        investigation_evidence_ids=["ev_cli"],
        source_files=["missing_loader.py"],
        preflight_commands={name: {"args": [sys.executable, "train.py"]} for name in STANDARD_PREFLIGHT_CHECKS},
    )

    inspected = ExecutorAdapter().inspect_draft(tmp_path, draft)

    assert inspected.status == "blocked"
    assert "draft source file is missing" in (inspected.blocker or "")


def test_adapter_preflight_runs_declared_checks_and_records_output(tmp_path: Path):
    (tmp_path / "run.py").write_text("print('preflight-ok')\n", encoding="utf-8")
    manifest = {
        "adapter_id": "generic_python",
        "entrypoint": "run.py",
        "smoke_argv": [sys.executable, "run.py"],
        "metrics_output": "metrics.json",
        "allowed_paths": ["run.py"],
        "protected_paths": ["run.py"],
        "preflight_required": True,
        "preflight_commands": {
            "help": {"args": [sys.executable, "run.py"]},
            "dataset_loader": {"args": [sys.executable, "run.py"]},
        },
    }
    (tmp_path / "autoad_executor_adapter.json").write_text(json.dumps(manifest), encoding="utf-8")

    inspected = ExecutorAdapter().inspect(tmp_path)
    result = run_adapter_preflight(tmp_path, inspected.evidence, required_checks=["help", "dataset_loader"])

    assert result.status == "passed"
    assert result.preflight_sha256 != "0" * 64
    assert [item.name for item in result.checks] == ["help", "dataset_loader"]
    assert result.checks[0].stdout == "preflight-ok\n"


def test_required_preflight_check_is_fail_closed(tmp_path: Path):
    (tmp_path / "run.py").write_text("print('ok')\n", encoding="utf-8")
    manifest = {
        "adapter_id": "generic_python", "entrypoint": "run.py", "smoke_argv": [sys.executable, "run.py"],
        "metrics_output": "metrics.json", "allowed_paths": ["run.py"], "protected_paths": ["run.py"],
        "preflight_required": True, "preflight_commands": {"help": {"args": [sys.executable, "run.py"]}},
    }
    (tmp_path / "autoad_executor_adapter.json").write_text(json.dumps(manifest), encoding="utf-8")

    inspected = ExecutorAdapter().inspect(tmp_path)
    result = run_adapter_preflight(tmp_path, inspected.evidence, required_checks=["help", "metrics"])

    assert result.status == "blocked"
    assert result.checks[-1].name == "preflight_contract"


def test_mandatory_preflight_is_recorded_in_preparation(tmp_path: Path):
    (tmp_path / "run.py").write_text("print('ok')\n", encoding="utf-8")
    manifest = {
        "adapter_id": "generic_python", "entrypoint": "run.py", "smoke_argv": [sys.executable, "run.py"],
        "metrics_output": "metrics.json", "allowed_paths": ["run.py"], "protected_paths": ["run.py"],
        "preflight_required": True, "preflight_commands": {"smoke": {"args": [sys.executable, "run.py"]}},
    }
    (tmp_path / "autoad_executor_adapter.json").write_text(json.dumps(manifest), encoding="utf-8")
    inspected = ExecutorAdapter().inspect(tmp_path)

    result = ensure_adapter_preflight(tmp_path, inspected.evidence, run_dir=tmp_path, artifact_name="fixture")

    assert result is not None and result.passed
    preparation = json.loads((tmp_path / "experiments" / "preparation.json").read_text(encoding="utf-8"))
    assert preparation["investigation_status"] == "complete"
    assert preparation["evidence"][0]["kind"] == "verified"


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
    with pytest.raises(ValueError, match="does not declare a split reference argument"):
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


@pytest.mark.parametrize(
    ("args", "index", "message"),
    [
        (["run.py", "--split=", ""], 1, "explicit empty argv slot"),
        (["run.py", "--split-file", "", "--other-split", ""], 2, "exactly one"),
    ],
)
def test_split_binding_fails_closed_for_untyped_or_ambiguous_shapes(
    tmp_path: Path, args: list[str], index: int, message: str
):
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
                    "b_dev": {
                        "args": args,
                        "metrics_output": "metrics.json",
                        "split_ref_arg_index": index,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    result = ExecutorAdapter().inspect(tmp_path)
    with pytest.raises(ValueError, match=message):
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
