"""Tests for internal PatchCore attempt helpers."""

from autoad_researcher.assets import asset_plan_sha256
from autoad_researcher.benchmarks.patchcore_attempt import (
    PATCHCORE_RESULT_CSV,
    WEIGHT_SHA256,
    build_patchcore_backbone_asset_plan,
    build_patchcore_command_plan,
    patchcore_metric_specs,
)
from autoad_researcher.runner import experiment_command_sha256


def test_patchcore_asset_plan_tracks_wideresnet_weight():
    plan = build_patchcore_backbone_asset_plan(run_id="run_demo")

    assert plan.network_during_execution is False
    assert plan.assets[0].expected_sha256 == WEIGHT_SHA256
    assert plan.assets[0].destination == (
        "assets/prepared/torch/hub/checkpoints/wide_resnet50_2-95faca4d.pth"
    )
    assert asset_plan_sha256(plan)


def test_patchcore_command_plan_is_locked_and_offline():
    plan = build_patchcore_command_plan(run_id="run_demo", attempt="attempt_01")

    assert plan.cwd == "runs/run_demo/attempt_01"
    assert plan.network is False
    assert plan.environment["TORCH_HOME"] == "assets/prepared/torch"
    assert plan.environment["HF_HUB_OFFLINE"] == "1"
    assert PATCHCORE_RESULT_CSV in plan.expected_outputs
    assert "wideresnet50" in plan.args
    assert "bottle" in plan.args
    assert experiment_command_sha256(plan)


def test_patchcore_metric_specs_parse_csv_result_source():
    specs = patchcore_metric_specs()

    assert [spec.metric_name for spec in specs] == [
        "instance_auroc",
        "full_pixel_auroc",
        "anomaly_pixel_auroc",
    ]
    assert specs[0].required is True
    assert all(spec.source_format == "csv" for spec in specs)
    assert all(spec.source_path == PATCHCORE_RESULT_CSV for spec in specs)
