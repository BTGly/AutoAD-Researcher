"""Explicit PatchCore-07H command adapter for an admitted intervention worktree."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.benchmarks.config import InternalBenchmarkCase
from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.benchmarks.patchcore_smoke import build_patchcore_smoke_command_plan, patchcore_smoke_metric_specs
from autoad_researcher.runner import ExperimentCommandPlan, ExperimentInputRefs, experiment_command_sha256


_ALLOWED = {"coreset_sampling_ratio", "target_embed_dimension", "patchsize", "anomaly_scorer_num_nn"}


class PatchCore07HAdapterInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    repository: Path
    benchmark_python: Path
    dataset_path: Path
    weight_path: Path
    environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_fingerprint: str = Field(pattern=r"^[0-9a-f]{40}$")
    allowed_parameters: list[str] = Field(min_length=1)
    parameter_overrides: dict[str, int | float] = Field(min_length=1)
    artifact_dir: Path

    @model_validator(mode="after")
    def _validate_override(self):
        if len(self.parameter_overrides) != 1:
            raise ValueError("PatchCore07H intervention requires exactly one parameter override")
        if not set(self.allowed_parameters) <= _ALLOWED:
            raise ValueError("allowed_parameters contains unsupported PatchCore07H fields")
        if not set(self.parameter_overrides) <= set(self.allowed_parameters):
            raise ValueError("parameter_overrides must be a subset of allowed_parameters")
        return self


class PatchCore07HExecutorAdapter:
    """Build a network-disabled Worker command from one admitted override only."""

    def __init__(self, *, case: InternalBenchmarkCase):
        self._case = case

    def build(self, inputs: PatchCore07HAdapterInputs) -> tuple[ExperimentCommandPlan, ExperimentInputRefs]:
        repository = inputs.repository.resolve()
        # Preserve a virtualenv launcher symlink.  ``resolve()`` turns
        # ``venv/bin/python`` into its base interpreter and drops the venv's
        # site-packages at process startup.
        benchmark_python = inputs.benchmark_python.absolute()
        dataset_path = inputs.dataset_path.resolve()
        weight_path = inputs.weight_path.resolve()
        entrypoint = repository / self._case.repository.entrypoint_path
        if not entrypoint.is_file():
            raise FileNotFoundError("PatchCore07H worktree is missing the frozen entrypoint")
        fixed = {**self._case.fixed_parameters, **inputs.parameter_overrides, "seed": 0}
        case = self._case.model_copy(update={"fixed_parameters": fixed})
        smoke = build_patchcore_smoke_command_plan(case=case, run_id=inputs.run_id, attempt="intervention_seed_0", dataset_path=str(dataset_path))
        command_file = (inputs.artifact_dir / "patchcore_command.json").resolve()
        command_file.parent.mkdir(parents=True, exist_ok=True)
        command_file.write_text(json.dumps({"command_id": smoke.command_id, "argv": [str(entrypoint), *smoke.args[1:]], "results_path": patchcore_smoke_metric_specs(case)[0].source_path, "metrics": [{"name": metric.name, "required": metric.required} for metric in case.evaluation.metrics], "protected_paths": case.evaluation.protected_paths, "parameter_overrides": inputs.parameter_overrides}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command_sha = sha256_file(command_file)
        plan = ExperimentCommandPlan(
            schema_version=1,
            command_id=smoke.command_id,
            program=str(benchmark_python),
            args=["-m", "autoad_researcher.benchmarks.patchcore_07h_runner", "--command-file", str(command_file), "--repository", str(repository), "--command-sha256", command_sha],
            cwd="attempts",
            environment={"PYTHONPATH": f"{Path(__file__).resolve().parents[3] / 'src'}:{repository / 'src'}", "TORCH_HOME": str(weight_path.parent.parent.parent), "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "0", "AUTOAD_PATCHCORE_COMMAND_SHA256": command_sha},
            timeout_seconds=1800,
            network=False,
            expected_outputs=[patchcore_smoke_metric_specs(case)[0].source_path, "metrics.json", "parsed_metrics.json", "protected_hash_before.json", "protected_hash_after.json", "command.json"],
        )
        return plan, ExperimentInputRefs(repository_fingerprint=inputs.repository_fingerprint, environment_sha256=inputs.environment_sha256, dataset_manifest_sha256=inputs.dataset_manifest_sha256, asset_manifest_sha256=inputs.asset_manifest_sha256, command_sha256=experiment_command_sha256(plan))
