from pathlib import Path

import pytest

from autoad_researcher.benchmarks.config import load_internal_benchmark_case
from autoad_researcher.benchmarks.patchcore_07h_executor_adapter import (
    PatchCore07HAdapterInputs,
    PatchCore07HExecutorAdapter,
)


def test_patchcore_07h_adapter_binds_one_approved_override_into_command(tmp_path: Path):
    repo = tmp_path / "patchcore"; (repo / "bin").mkdir(parents=True); (repo / "src").mkdir()
    (repo / "bin" / "run_patchcore.py").write_text("# fixture\n", encoding="utf-8")
    case = load_internal_benchmark_case(Path("configs/benchmarks/internal_patchcore_mvtec_bottle_smoke_v1.yaml"))
    plan, refs = PatchCore07HExecutorAdapter(case=case).build(
        PatchCore07HAdapterInputs(
            run_id="07h", attempt_id="attempt_000006", repository=repo,
            benchmark_python=Path("workspace/envs/patchcore/python"), dataset_path=tmp_path / "b_dev",
            weight_path=Path("workspace/cache/weight.pth"), environment_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64, asset_manifest_sha256="c" * 64,
            repository_fingerprint="d" * 40,
            allowed_parameters=["coreset_sampling_ratio"],
            parameter_overrides={"coreset_sampling_ratio": 0.2}, artifact_dir=tmp_path / "artifact",
        )
    )
    command = (tmp_path / "artifact" / "patchcore_command.json").read_text(encoding="utf-8")
    assert '"0.2"' in command
    assert plan.command_id.startswith("intervention_seed_0_")
    assert refs.command_sha256
    assert Path(plan.program).is_absolute()
    assert Path(plan.args[3]).is_absolute()
    assert Path(plan.args[5]).is_absolute()


def test_patchcore_07h_adapter_rejects_unapproved_or_multi_parameter_override(tmp_path: Path):
    case = load_internal_benchmark_case(Path("configs/benchmarks/internal_patchcore_mvtec_bottle_smoke_v1.yaml"))
    base = dict(run_id="07h", attempt_id="attempt_000006", repository=tmp_path, benchmark_python=Path("/opt/python"), dataset_path=tmp_path, weight_path=tmp_path / "w", environment_sha256="a" * 64, dataset_manifest_sha256="b" * 64, asset_manifest_sha256="c" * 64, repository_fingerprint="d" * 40, allowed_parameters=["patchsize"], artifact_dir=tmp_path / "artifact")
    with pytest.raises(ValueError, match="allowed_parameters"):
        PatchCore07HExecutorAdapter(case=case).build(PatchCore07HAdapterInputs(**base, parameter_overrides={"coreset_sampling_ratio": .2}))
    with pytest.raises(ValueError, match="exactly one"):
        PatchCore07HExecutorAdapter(case=case).build(PatchCore07HAdapterInputs(**base, parameter_overrides={"patchsize": 3, "anomaly_scorer_num_nn": 1}))


def test_patchcore_07h_adapter_preserves_virtualenv_python_symlink(tmp_path: Path):
    """Resolving the launcher would discard the virtualenv's site-packages."""
    repo = tmp_path / "patchcore"; (repo / "bin").mkdir(parents=True)
    (repo / "bin" / "run_patchcore.py").write_text("# fixture\n", encoding="utf-8")
    target = tmp_path / "base-python"; target.write_text("fixture\n", encoding="utf-8")
    launcher = tmp_path / "venv" / "bin" / "python"; launcher.parent.mkdir(parents=True)
    launcher.symlink_to(target)
    case = load_internal_benchmark_case(Path("configs/benchmarks/internal_patchcore_mvtec_bottle_smoke_v1.yaml"))

    plan, _ = PatchCore07HExecutorAdapter(case=case).build(
        PatchCore07HAdapterInputs(
            run_id="07h", attempt_id="attempt_000008", repository=repo,
            benchmark_python=launcher, dataset_path=tmp_path / "b_dev", weight_path=tmp_path / "weight.pth",
            environment_sha256="a" * 64, dataset_manifest_sha256="b" * 64, asset_manifest_sha256="c" * 64,
            repository_fingerprint="d" * 40, allowed_parameters=["patchsize"],
            parameter_overrides={"patchsize": 2}, artifact_dir=tmp_path / "artifact",
        )
    )

    assert plan.program == str(launcher.absolute())
