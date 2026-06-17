"""Tests for the internal PatchCore attempt orchestration script."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from autoad_researcher.analysis.metrics import MetricsReport
from autoad_researcher.assets import AssetManifest
from autoad_researcher.benchmarks.evidence import (
    BenchmarkDatasetFileEntry,
    BenchmarkDatasetManifest,
    BenchmarkEnvironmentSnapshot,
    BenchmarkPreflightCheck,
    BenchmarkPreflightReport,
    BenchmarkRepositoryState,
)
from autoad_researcher.runner import ExperimentExecutionResult
from autoad_researcher.supervisor import ScientificValidityReport
from scripts.benchmark import run_internal_patchcore_attempt as script


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
COMMIT = "a" * 40


def args(**overrides):
    data = {
        "run_id": "run_ok",
        "attempt": "attempt_01",
        "case": "case.yaml",
        "repo": "workspace/repos/patchcore-inspection",
        "benchmark_python": "workspace/envs/patchcore_linux_gpu/bin/python",
        "lockfile": "configs/benchmarks/environments/patchcore_linux_gpu/requirements.lock.txt",
        "weight_source": "cache/torch_probe/hub/checkpoints/wide_resnet50_2-95faca4d.pth",
        "dataset_root": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def fake_case():
    return SimpleNamespace(
        case_id="internal_patchcore_mvtec_bottle_v1",
        dataset=SimpleNamespace(root_env="DS_ROOT", category="bottle"),
        fixed_parameters={"seed": 0},
        baseline_name="PatchCore",
    )


def failed_preflight_bundle():
    report = BenchmarkPreflightReport(
        schema_version=1,
        case_id="internal_patchcore_mvtec_bottle_v1",
        attempt="attempt_01",
        checks=[
            BenchmarkPreflightCheck(
                name="dataset",
                status="failed",
                code="DATASET_ROOT_ENV_MISSING",
                message="dataset root missing",
            )
        ],
        passed=False,
    )
    return SimpleNamespace(
        report=report,
        repository_state=None,
        dataset_manifest=None,
        environment_snapshot=None,
    )


def passed_preflight_bundle():
    report = BenchmarkPreflightReport(
        schema_version=1,
        case_id="internal_patchcore_mvtec_bottle_v1",
        attempt="attempt_01",
        checks=[
            BenchmarkPreflightCheck(
                name="all",
                status="passed",
                code="OK",
                message="ok",
            )
        ],
        passed=True,
    )
    repo = BenchmarkRepositoryState(
        schema_version=1,
        case_id="internal_patchcore_mvtec_bottle_v1",
        expected_commit=COMMIT,
        actual_commit=COMMIT,
        detached_head=True,
        dirty=False,
        remote_url="github.com/amazon-science/patchcore-inspection",
        required_files=[],
        repository_fingerprint=SHA_A,
    )
    dataset = BenchmarkDatasetManifest(
        schema_version=1,
        dataset_name="MVTec AD",
        category="bottle",
        root_env="DS_ROOT",
        files=[BenchmarkDatasetFileEntry(relative_path="bottle/train/good/001.png", size_bytes=1)],
        train_good_count=1,
        test_good_count=1,
        test_anomaly_count=1,
        mask_count=1,
        manifest_sha256=SHA_B,
    )
    env = BenchmarkEnvironmentSnapshot(
        schema_version=1,
        python_version="3.11.15",
        platform="linux_x86_64",
        accelerator="cuda",
        torch_version="2.5.1+cu124",
        torchvision_version="0.20.1+cu124",
        cuda_available=True,
        cuda_device_count=1,
        gpu_index=0,
        lockfile_sha256=SHA_C,
        environment_sha256=SHA_C,
    )
    return SimpleNamespace(
        report=report,
        repository_state=repo,
        dataset_manifest=dataset,
        environment_snapshot=env,
    )


def prepared_asset_manifest() -> AssetManifest:
    return AssetManifest.model_validate(
        {
            "schema_version": 1,
            "plan_id": "patchcore_wideresnet50_assets",
            "run_id": "run_ok",
            "assets": [
                {
                    "asset_id": "torchvision_wideresnet50_imagenet1k_v1",
                    "kind": "model_weight",
                    "source": {"source_type": "local_path", "uri": "cache/weight.pth"},
                    "path": "assets/prepared/torch/hub/checkpoints/weight.pth",
                    "sha256": SHA_D,
                    "required": True,
                    "status": "prepared",
                }
            ],
            "manifest_sha256": SHA_D,
        }
    )


def failed_asset_manifest() -> AssetManifest:
    return AssetManifest.model_validate(
        {
            "schema_version": 1,
            "plan_id": "patchcore_wideresnet50_assets",
            "run_id": "run_ok",
            "assets": [
                {
                    "asset_id": "torchvision_wideresnet50_imagenet1k_v1",
                    "kind": "model_weight",
                    "source": {"source_type": "local_path", "uri": "cache/missing.pth"},
                    "path": "assets/prepared/torch/hub/checkpoints/weight.pth",
                    "sha256": None,
                    "required": True,
                    "status": "failed",
                    "failure_code": "LOCAL_SOURCE_MISSING",
                    "failure_message": "missing local asset",
                }
            ],
            "manifest_sha256": SHA_D,
        }
    )


def metrics_report() -> MetricsReport:
    return MetricsReport.model_validate(
        {
            "schema_version": 1,
            "metrics": [],
            "required_parsed": 0,
            "required_total": 0,
            "status": "passed",
            "report_sha256": SHA_A,
        }
    )


def validity_report() -> ScientificValidityReport:
    return ScientificValidityReport(schema_version=1, status="insufficient_evidence", checks=[])


def test_invalid_run_id_rejected_before_creating_dirs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(script, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ValueError, match="run_id"):
        script._run(args(run_id="../../outside"))

    assert not (tmp_path / "outside").exists()


def test_preflight_failure_writes_only_preflight_evidence(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(script, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(script, "load_internal_benchmark_case", lambda path: fake_case())
    monkeypatch.setattr(script, "run_preflight", lambda **kwargs: failed_preflight_bundle())

    summary = script._run(args())

    assert summary["attempt_status"] == "preflight_failed"
    assert (tmp_path / "runs/run_ok/preflight_attempt_01/preflight_report.json").is_file()
    assert not (tmp_path / "runs/run_ok/attempt_01").exists()


def test_dataset_root_drives_preflight_and_command_plan(tmp_path: Path, monkeypatch):
    dataset_root = tmp_path / "workspace/datasets/custom_mvtec"
    dataset_root.mkdir(parents=True)
    captured: dict[str, object] = {}
    original_build_command_plan = script.build_patchcore_command_plan

    monkeypatch.setattr(script, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(script, "load_internal_benchmark_case", lambda path: fake_case())

    def fake_run_preflight(**kwargs):
        captured["preflight_environ"] = kwargs["environ"]
        return passed_preflight_bundle()

    def fake_prepare_assets(*args, **kwargs):
        return prepared_asset_manifest()

    def fake_command_plan(*, run_id, attempt, dataset_path):
        captured["dataset_path"] = dataset_path
        return original_build_command_plan(
            run_id=run_id,
            attempt=attempt,
            dataset_path=dataset_path,
        )

    def fake_execute(*, run_id, attempt, command_plan, input_refs, attempt_dir, runner, repository_fingerprint_after):
        attempt_dir.mkdir(parents=True)
        repository_fingerprint_after()
        return ExperimentExecutionResult(
            schema_version=1,
            run_id=run_id,
            attempt=attempt,
            command_id=command_plan.command_id,
            command_sha256=input_refs.command_sha256,
            status="success",
            exit_code=0,
            timed_out=False,
            stdout_path="stdout.log",
            stderr_path="stderr.log",
            output_manifest_path="output_manifest.json",
        )

    monkeypatch.setattr(script, "run_preflight", fake_run_preflight)
    monkeypatch.setattr(script, "prepare_assets", fake_prepare_assets)
    monkeypatch.setattr(script, "build_patchcore_command_plan", fake_command_plan)
    monkeypatch.setattr(script, "execute_experiment_attempt", fake_execute)
    monkeypatch.setattr(script, "parse_metrics", lambda attempt_dir, specs: metrics_report())
    monkeypatch.setattr(script, "validate_scientific_contract", lambda **kwargs: validity_report())
    monkeypatch.setattr(
        script,
        "collect_repository_state",
        lambda **kwargs: SimpleNamespace(repository_fingerprint=SHA_A),
    )

    summary = script._run(args(dataset_root="workspace/datasets/custom_mvtec"))

    assert summary["attempt_status"] == "success"
    assert captured["preflight_environ"]["DS_ROOT"] == str(dataset_root)
    assert captured["dataset_path"] == "../../../workspace/datasets/custom_mvtec"
    assert (tmp_path / "runs/run_ok/attempt_01/command.json").is_file()


def test_required_asset_failure_stops_before_attempt_dir(tmp_path: Path, monkeypatch):
    dataset_root = tmp_path / "workspace/datasets/custom_mvtec"
    dataset_root.mkdir(parents=True)

    monkeypatch.setattr(script, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(script, "load_internal_benchmark_case", lambda path: fake_case())
    monkeypatch.setattr(script, "run_preflight", lambda **kwargs: passed_preflight_bundle())

    def fake_prepare_assets(*args, **kwargs):
        manifest = failed_asset_manifest()
        script.write_json_atomic(kwargs["manifest_path"], manifest)
        return manifest

    monkeypatch.setattr(script, "prepare_assets", fake_prepare_assets)

    summary = script._run(args(dataset_root="workspace/datasets/custom_mvtec"))

    assert summary["attempt_status"] == "asset_prepare_failed"
    assert (tmp_path / "runs/run_ok/assets/asset_manifest.json").is_file()
    assert not (tmp_path / "runs/run_ok/attempt_01").exists()
