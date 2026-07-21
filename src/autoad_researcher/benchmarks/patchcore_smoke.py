"""Configuration-driven command contracts for the physical 07H smoke case.

This module deliberately does not alter the older locked internal PatchCore
benchmark.  The 07H smoke case has its own config and every command-relevant
value is read from that config, so its command and evaluation evidence cannot
silently diverge.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.analysis import MetricParseSpec
from autoad_researcher.runner import ExperimentCommandPlan
from autoad_researcher.schemas import InternalBenchmarkCase

_PATCHCORE_METRICS = {
    "instance_auroc",
    "full_pixel_auroc",
    "anomaly_pixel_auroc",
}
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


class PatchCoreSmokeFixedParameters(BaseModel):
    """All fixed values that affect a physical 07H PatchCore command."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    seed: int = Field(ge=0)
    backbone: str = Field(min_length=1)
    layers: list[str] = Field(min_length=1)
    resize: int = Field(gt=0)
    imagesize: int = Field(gt=0)
    pretrain_embed_dimension: int = Field(gt=0)
    target_embed_dimension: int = Field(gt=0)
    anomaly_scorer_num_nn: int = Field(gt=0)
    patchsize: int = Field(gt=0)
    sampler: Literal["identity", "greedy_coreset", "approx_greedy_coreset"]
    coreset_sampling_ratio: float = Field(gt=0, le=1)
    batch_size: int = Field(gt=0)
    num_workers: int = Field(ge=0)
    train_val_split: float = Field(gt=0, le=1)
    faiss_on_gpu: bool
    gpu: Literal[0]
    preprocessing: Literal["mean", "conv"]
    aggregation: Literal["mean", "mlp"]
    patchscore: str = Field(min_length=1)
    patchoverlap: float = Field(ge=0)
    faiss_num_workers: int = Field(ge=0)
    augment: bool
    log_project: str = Field(pattern=_IDENTIFIER)
    log_group: str = Field(pattern=_IDENTIFIER)
    results_path: str = Field(min_length=1)
    save_patchcore_model: bool
    save_segmentation_images: bool
    attempt_timeout_seconds: int = Field(gt=0)

    @field_validator("results_path")
    @classmethod
    def _validate_results_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value in {"", "."}:
            raise ValueError("results_path must be a non-empty relative path")
        return value


def build_patchcore_smoke_command_plan(
    *,
    case: InternalBenchmarkCase,
    run_id: str,
    attempt: str,
    dataset_path: str,
) -> ExperimentCommandPlan:
    """Build a PatchCore command solely from the frozen 07H smoke case."""
    parameters = _parameters(case)
    expected_output = _expected_result_path(parameters)
    _require_result_path_contract(case, expected_output)

    args = [
        "../../../workspace/repos/patchcore-inspection/bin/run_patchcore.py",
        "--gpu",
        str(parameters.gpu),
        "--seed",
        str(parameters.seed),
        "--log_group",
        parameters.log_group,
        "--log_project",
        parameters.log_project,
    ]
    if parameters.save_segmentation_images:
        args.append("--save_segmentation_images")
    if parameters.save_patchcore_model:
        args.append("--save_patchcore_model")
    args.extend(
        [
            parameters.results_path,
            "patch_core",
            "-b",
            parameters.backbone,
        ]
    )
    for layer in parameters.layers:
        args.extend(["-le", layer])
    args.extend(
        [
            "--pretrain_embed_dimension",
            str(parameters.pretrain_embed_dimension),
            "--target_embed_dimension",
            str(parameters.target_embed_dimension),
            "--preprocessing",
            parameters.preprocessing,
            "--aggregation",
            parameters.aggregation,
            "--anomaly_scorer_num_nn",
            str(parameters.anomaly_scorer_num_nn),
            "--patchsize",
            str(parameters.patchsize),
            "--patchscore",
            parameters.patchscore,
            "--patchoverlap",
            str(parameters.patchoverlap),
        ]
    )
    if parameters.faiss_on_gpu:
        args.append("--faiss_on_gpu")
    args.extend(
        [
            "--faiss_num_workers",
            str(parameters.faiss_num_workers),
            "sampler",
            "--percentage",
            str(parameters.coreset_sampling_ratio),
            parameters.sampler,
            "dataset",
            "--resize",
            str(parameters.resize),
            "--imagesize",
            str(parameters.imagesize),
            "-d",
            case.dataset.category,
            "--train_val_split",
            str(parameters.train_val_split),
            "--batch_size",
            str(parameters.batch_size),
            "--num_workers",
            str(parameters.num_workers),
        ]
    )
    if parameters.augment:
        args.append("--augment")
    args.extend(["mvtec", dataset_path])

    return ExperimentCommandPlan.model_validate(
        {
            "schema_version": 1,
            "command_id": f"{attempt}_{case.case_id}",
            "program": "../../../workspace/envs/patchcore_linux_gpu/bin/python",
            "args": args,
            "cwd": f"runs/{run_id}/{attempt}",
            "environment": {
                "PYTHONPATH": "../../../workspace/repos/patchcore-inspection/src",
                "TORCH_HOME": "../assets/prepared/torch",
                "PYTHONHASHSEED": str(parameters.seed),
                "PYTHONDONTWRITEBYTECODE": "1",
                "MPLCONFIGDIR": "matplotlib",
                "HF_HUB_OFFLINE": "1",
                "CUDA_VISIBLE_DEVICES": "0",
            },
            "timeout_seconds": parameters.attempt_timeout_seconds,
            "network": False,
            "expected_outputs": [expected_output],
        }
    )


def patchcore_smoke_metric_specs(case: InternalBenchmarkCase) -> list[MetricParseSpec]:
    """Build metric parsing specs only when the case output contract matches."""
    parameters = _parameters(case)
    expected_output = _expected_result_path(parameters)
    _require_result_path_contract(case, expected_output)
    metric_names = [metric.name for metric in case.evaluation.metrics]
    unsupported = sorted(set(metric_names).difference(_PATCHCORE_METRICS))
    if unsupported:
        raise ValueError(f"unsupported PatchCore CSV metrics: {unsupported}")
    return [
        MetricParseSpec(
            metric_name=metric.name,
            source_path=expected_output,
            source_format="csv",
            csv_row_key="Row Names",
            csv_row_value=f"mvtec_{case.dataset.category}",
            csv_metric_column=metric.name,
            dataset_row=f"mvtec_{case.dataset.category}",
            unit=metric.unit,
            required=metric.required,
        )
        for metric in case.evaluation.metrics
    ]


def _parameters(case: InternalBenchmarkCase) -> PatchCoreSmokeFixedParameters:
    return PatchCoreSmokeFixedParameters.model_validate(case.fixed_parameters)


def _expected_result_path(parameters: PatchCoreSmokeFixedParameters) -> str:
    return (
        f"{parameters.results_path}/{parameters.log_project}/"
        f"{parameters.log_group}/results.csv"
    )


def _require_result_path_contract(case: InternalBenchmarkCase, expected_output: str) -> None:
    raw_paths = case.evaluation.raw_result_paths
    if raw_paths != [expected_output]:
        raise ValueError(
            "PatchCore smoke evaluation.raw_result_paths must contain exactly "
            f"{expected_output!r}"
        )
