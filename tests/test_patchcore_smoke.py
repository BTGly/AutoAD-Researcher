"""07H PatchCore smoke protocol tests."""

from pathlib import Path

import pytest

from autoad_researcher.benchmarks.config import load_internal_benchmark_case
from autoad_researcher.benchmarks.patchcore_smoke import (
    build_patchcore_smoke_command_plan,
    patchcore_smoke_metric_specs,
)
from autoad_researcher.runner import experiment_command_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = PROJECT_ROOT / "configs/benchmarks/internal_patchcore_mvtec_bottle_smoke_v1.yaml"


def _case():
    return load_internal_benchmark_case(CASE_PATH)


def test_smoke_case_command_exactly_uses_frozen_parameters():
    case = _case()
    plan = build_patchcore_smoke_command_plan(
        case=case,
        run_id="run_07h",
        attempt="baseline_seed_0",
        dataset_path="../../../workspace/datasets/07h/b_dev",
    )

    assert plan.command_id == "baseline_seed_0_internal_patchcore_mvtec_bottle_smoke_v1"
    assert plan.cwd == "runs/run_07h/baseline_seed_0"
    assert plan.timeout_seconds == 1800
    assert plan.network is False
    assert plan.environment["CUDA_VISIBLE_DEVICES"] == "0"
    assert plan.environment["PYTHONHASHSEED"] == "0"
    assert plan.expected_outputs == [
        "raw/autoad_07h_physical/internal_patchcore_mvtec_bottle_smoke_v1/results.csv"
    ]
    assert plan.args == [
        "../../../workspace/repos/patchcore-inspection/bin/run_patchcore.py",
        "--gpu", "0", "--seed", "0",
        "--log_group", "internal_patchcore_mvtec_bottle_smoke_v1",
        "--log_project", "autoad_07h_physical",
        "raw",
        "patch_core",
        "-b", "wideresnet50",
        "-le", "layer2", "-le", "layer3",
        "--pretrain_embed_dimension", "1024",
        "--target_embed_dimension", "1024",
        "--preprocessing", "mean",
        "--aggregation", "mean",
        "--anomaly_scorer_num_nn", "1",
        "--patchsize", "3",
        "--patchscore", "max",
        "--patchoverlap", "0.0",
        "--faiss_num_workers", "2",
        "sampler",
        "--percentage", "0.1",
        "approx_greedy_coreset",
        "dataset",
        "--resize", "128",
        "--imagesize", "128",
        "-d", "bottle",
        "--train_val_split", "1.0",
        "--batch_size", "2",
        "--num_workers", "2",
        "mvtec",
        "../../../workspace/datasets/07h/b_dev",
    ]
    assert experiment_command_sha256(plan)


def test_smoke_metric_specs_share_the_frozen_result_contract():
    specs = patchcore_smoke_metric_specs(_case())

    assert [spec.metric_name for spec in specs] == [
        "instance_auroc",
        "full_pixel_auroc",
        "anomaly_pixel_auroc",
    ]
    assert {spec.source_path for spec in specs} == {
        "raw/autoad_07h_physical/internal_patchcore_mvtec_bottle_smoke_v1/results.csv"
    }
    assert {spec.dataset_row for spec in specs} == {"mvtec_bottle"}


def test_smoke_builder_rejects_missing_command_parameter():
    case = _case()
    parameters = dict(case.fixed_parameters)
    parameters.pop("resize")
    malformed = case.model_copy(update={"fixed_parameters": parameters})

    with pytest.raises(Exception, match="resize"):
        build_patchcore_smoke_command_plan(
            case=malformed,
            run_id="run_07h",
            attempt="baseline_seed_0",
            dataset_path="dataset",
        )


def test_smoke_builder_rejects_evaluation_output_mismatch():
    case = _case()
    evaluation = case.evaluation.model_copy(update={"raw_result_paths": ["other.csv"]})
    malformed = case.model_copy(update={"evaluation": evaluation})

    with pytest.raises(ValueError, match="raw_result_paths"):
        build_patchcore_smoke_command_plan(
            case=malformed,
            run_id="run_07h",
            attempt="baseline_seed_0",
            dataset_path="dataset",
        )
