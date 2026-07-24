"""Thin, evidence-led Executor adapters; they neither create Jobs nor run code."""
from __future__ import annotations
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from autoad_researcher.runner import ExperimentCommandPlan, ExperimentInputRefs, experiment_command_sha256

_CONFIG = "autoad_executor_adapter.json"
_PYTHON_RUNTIME_ENV = "PYTHONDONTWRITEBYTECODE"


class ExecutorEvaluationCommand(BaseModel):
    """One repository-declared command for a named evaluation phase.

    A held-out evaluation must not be reconstructed from a path or prose.  The
    adapter manifest therefore carries the exact argv/environment it supports
    for that phase, just as the normal adapter contract carries its entrypoint.
    """

    model_config = ConfigDict(extra="forbid")

    args: list[str] = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    metrics_output: str = Field(min_length=1)
    split_ref_arg_index: int | None = Field(default=None, ge=0)


class ExecutorAdapterEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter_id: Literal["generic_python", "patchcore_style", "anomalib_style"]
    entrypoint: str
    smoke_argv: list[str] = Field(min_length=1)
    metrics_output: str
    allowed_paths: list[str] = Field(min_length=1)
    protected_paths: list[str] = Field(min_length=1)
    activation_evidence: Literal["observed", "unverified"] = "unverified"
    evaluation_commands: dict[Literal["b_dev", "b_test"], ExecutorEvaluationCommand] = Field(default_factory=dict)

class ExecutorAdapterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["supported", "blocked"]
    adapter_id: str | None = None
    blocker: str | None = None
    evidence: ExecutorAdapterEvidence | None = None

class ExecutorAdapterInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1)
    worktree_ref: str = Field(min_length=1)
    environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_fingerprint: str = Field(min_length=1)
    python_executable: str = Field(default_factory=lambda: sys.executable, min_length=1)
    timeout_seconds: int = Field(default=60, gt=0)
    evaluation_phase: Literal["b_dev", "b_test"] = "b_dev"
    split_ref: str | None = None

class ExecutorAdapter:
    """Read one explicit repository-local adapter manifest, never infer an argv."""
    def inspect(self, repository_root: Path) -> ExecutorAdapterResult:
        manifest = repository_root / _CONFIG
        if not manifest.is_file():
            return ExecutorAdapterResult(status="blocked", blocker=f"missing explicit {_CONFIG}")
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            evidence = ExecutorAdapterEvidence.model_validate(raw)
            for path in [
                evidence.entrypoint,
                evidence.metrics_output,
                *evidence.allowed_paths,
                *evidence.protected_paths,
                *[command.metrics_output for command in evidence.evaluation_commands.values()],
            ]:
                _safe_relative(path)
                if not (repository_root / path).is_file() and path in {evidence.entrypoint, *evidence.protected_paths}:
                    raise ValueError(f"declared file is missing: {path}")
        except Exception as exc:
            return ExecutorAdapterResult(status="blocked", blocker=f"invalid adapter evidence: {exc}")
        return ExecutorAdapterResult(status="supported", adapter_id=evidence.adapter_id, evidence=evidence)

    def build_execution(self, result: ExecutorAdapterResult, inputs: ExecutorAdapterInputs) -> tuple[ExperimentCommandPlan, ExperimentInputRefs]:
        if result.status != "supported" or result.evidence is None:
            raise ValueError(result.blocker or "adapter is unsupported")
        evidence = result.evidence
        phase_command = evidence.evaluation_commands.get(inputs.evaluation_phase)
        if phase_command is None:
            if inputs.split_ref is not None:
                raise ValueError(
                    f"adapter has no explicit {inputs.evaluation_phase} command for the frozen split"
                )
            args, environment, metrics_output = [evidence.entrypoint], _python_environment({}), evidence.metrics_output
        else:
            args = list(phase_command.args)
            if inputs.split_ref is not None:
                index = phase_command.split_ref_arg_index
                if index is None:
                    raise ValueError(
                        f"adapter {inputs.evaluation_phase} command does not declare a split reference argument"
                    )
                if index >= len(args):
                    raise ValueError("adapter split reference argument index is outside the declared command")
                if args[index] != "":
                    raise ValueError(
                        "adapter split reference binding must target an explicit empty argv slot"
                    )
                if index > 0 and args[index - 1].endswith("="):
                    raise ValueError(
                        "adapter split reference binding does not support equals-form arguments"
                    )
                if args.count("") != 1:
                    raise ValueError(
                        "adapter split reference binding requires exactly one explicit empty argv slot"
                    )
                args[index] = inputs.split_ref
            environment, metrics_output = _python_environment(phase_command.environment), phase_command.metrics_output
        plan = ExperimentCommandPlan(schema_version=1, command_id=f"{evidence.adapter_id}_{inputs.evaluation_phase}", program=inputs.python_executable, args=args, cwd=inputs.worktree_ref, environment=environment, timeout_seconds=inputs.timeout_seconds, network=False, expected_outputs=[metrics_output])
        refs = ExperimentInputRefs(repository_fingerprint=inputs.repository_fingerprint, environment_sha256=inputs.environment_sha256, dataset_manifest_sha256=inputs.dataset_manifest_sha256, asset_manifest_sha256=inputs.asset_manifest_sha256, command_sha256=experiment_command_sha256(plan))
        return plan, refs

def _safe_relative(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part == ".." for part in path.parts) or not path.parts:
        raise ValueError("adapter paths must be repository-relative")


def _python_environment(environment: dict[str, str]) -> dict[str, str]:
    result = dict(environment)
    result[_PYTHON_RUNTIME_ENV] = "1"
    return result
