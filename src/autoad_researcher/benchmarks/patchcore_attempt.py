"""Internal PatchCore benchmark attempt helpers."""

from typing import Literal

from autoad_researcher.analysis import MetricParseSpec
from autoad_researcher.assets import AssetPlan
from autoad_researcher.runner import ExperimentCommandPlan

CASE_ID = "internal_patchcore_mvtec_bottle_v1"
RUN_PROJECT = "autoad_internal_benchmark"
RUN_GROUP = CASE_ID
WEIGHT_FILENAME = "wide_resnet50_2-95faca4d.pth"
WEIGHT_SHA256 = "95faca4d11227dddf8633dbb5ff6c8a9003c1aa5b8945c73834b8007b10950b8"
PATCHCORE_RESULT_CSV = f"raw/{RUN_PROJECT}/{RUN_GROUP}/results.csv"


def build_patchcore_backbone_asset_plan(
    *,
    run_id: str,
    source_uri: str = f"cache/torch_probe/hub/checkpoints/{WEIGHT_FILENAME}",
) -> AssetPlan:
    """Build the internal PatchCore backbone AssetPlan."""
    return AssetPlan.model_validate(
        {
            "schema_version": 1,
            "plan_id": "patchcore_wideresnet50_assets",
            "run_id": run_id,
            "network_during_prepare": False,
            "network_during_execution": False,
            "assets": [
                {
                    "asset_id": "torchvision_wideresnet50_imagenet1k_v1",
                    "kind": "model_weight",
                    "source": {
                        "source_type": "local_path",
                        "uri": source_uri,
                        "description": "torchvision Wide_ResNet50_2 IMAGENET1K_V1 weight",
                    },
                    "destination": f"assets/prepared/torch/hub/checkpoints/{WEIGHT_FILENAME}",
                    "expected_sha256": WEIGHT_SHA256,
                    "required": True,
                    "validation": [
                        {
                            "validation_id": "weight_sha256",
                            "kind": "sha256",
                            "parameters": {"sha256": WEIGHT_SHA256},
                            "required": True,
                            "network": False,
                        },
                        {
                            "validation_id": "weight_file_exists",
                            "kind": "file_exists",
                            "parameters": {},
                            "required": True,
                            "network": False,
                        },
                    ],
                }
            ],
        }
    )


def build_patchcore_command_plan(
    *,
    run_id: str,
    attempt: Literal["attempt_01", "attempt_02"],
    dataset_path: str = "../../../workspace/datasets/mvtec",
) -> ExperimentCommandPlan:
    """Build the locked PatchCore/MVTec bottle command plan."""
    return ExperimentCommandPlan.model_validate(
        {
            "schema_version": 1,
            "command_id": f"{attempt}_patchcore_mvtec_bottle",
            "program": "../../../workspace/envs/patchcore_linux_gpu/bin/python",
            "args": [
                "../../../workspace/repos/patchcore-inspection/bin/run_patchcore.py",
                "--gpu",
                "0",
                "--seed",
                "0",
                "--log_group",
                RUN_GROUP,
                "--log_project",
                RUN_PROJECT,
                "raw",
                "patch_core",
                "-b",
                "wideresnet50",
                "-le",
                "layer2",
                "-le",
                "layer3",
                "--pretrain_embed_dimension",
                "1024",
                "--target_embed_dimension",
                "1024",
                "--anomaly_scorer_num_nn",
                "1",
                "--patchsize",
                "3",
                "--patchscore",
                "max",
                "--patchoverlap",
                "0.0",
                "--faiss_num_workers",
                "8",
                "sampler",
                "-p",
                "0.1",
                "approx_greedy_coreset",
                "dataset",
                "--resize",
                "256",
                "--imagesize",
                "224",
                "-d",
                "bottle",
                "--train_val_split",
                "1.0",
                "--batch_size",
                "2",
                "--num_workers",
                "8",
                "mvtec",
                dataset_path,
            ],
            "cwd": f"runs/{run_id}/{attempt}",
            "environment": {
                "PYTHONPATH": "../../../workspace/repos/patchcore-inspection/src",
                "TORCH_HOME": "assets/prepared/torch",
                "PYTHONHASHSEED": "0",
                "PYTHONDONTWRITEBYTECODE": "1",
                "MPLCONFIGDIR": "matplotlib",
                "HF_HUB_OFFLINE": "1",
                "CUDA_VISIBLE_DEVICES": "0",
            },
            "timeout_seconds": 7200,
            "network": False,
            "expected_outputs": [PATCHCORE_RESULT_CSV],
        }
    )


def patchcore_metric_specs() -> list[MetricParseSpec]:
    """Metric parser specs for PatchCore results.csv."""
    return [
        MetricParseSpec(
            metric_name=name,
            source_path=PATCHCORE_RESULT_CSV,
            source_format="csv",
            csv_row_key="Row Names",
            csv_row_value="mvtec_bottle",
            csv_metric_column=name,
            dataset_row="mvtec_bottle",
            unit="ratio",
            required=required,
        )
        for name, required in [
            ("instance_auroc", True),
            ("full_pixel_auroc", False),
            ("anomaly_pixel_auroc", False),
        ]
    ]
