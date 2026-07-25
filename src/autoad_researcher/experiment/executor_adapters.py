"""Thin, evidence-led Executor adapters; they neither create Jobs nor run code."""
from __future__ import annotations
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from autoad_researcher.runner import ExperimentCommandPlan, ExperimentInputRefs, experiment_command_sha256

_CONFIG = "autoad_executor_adapter.json"
_DRAFT_CONFIG = "autoad_executor_adapter_draft.json"
STANDARD_PREFLIGHT_CHECKS = [
    "entrypoint",
    "dataset_loader",
    "help",
    "smoke",
    "data_loading",
    "checkpoint_output",
    "metrics_artifact",
]


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


class ExecutorPreflightCommand(BaseModel):
    """One repository-declared, bounded command used before training."""

    model_config = ConfigDict(extra="forbid")

    args: list[str] = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    required: bool = True


class ExecutorAdapterDraft(BaseModel):
    """Agent-produced adapter proposal.

    A draft is input to backend validation only.  It is never treated as a
    frozen execution contract until the declared preflight checks pass.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    adapter_id: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    smoke_argv: list[str] = Field(min_length=1)
    metrics_output: str = Field(min_length=1)
    allowed_paths: list[str] = Field(min_length=1)
    protected_paths: list[str] = Field(min_length=1)
    activation_evidence: Literal["observed", "inferred"] = "observed"
    investigation_evidence_ids: list[str] = Field(min_length=1)
    source_files: list[str] = Field(min_length=1)
    evaluation_commands: dict[str, ExecutorEvaluationCommand] = Field(default_factory=dict)
    preflight_commands: dict[str, ExecutorPreflightCommand] = Field(default_factory=dict)

    def to_evidence(self) -> "ExecutorAdapterEvidence":
        return ExecutorAdapterEvidence(
            adapter_id=self.adapter_id,
            entrypoint=self.entrypoint,
            smoke_argv=self.smoke_argv,
            metrics_output=self.metrics_output,
            allowed_paths=self.allowed_paths,
            protected_paths=self.protected_paths,
            activation_evidence=self.activation_evidence,
            evaluation_commands=self.evaluation_commands,
            preflight_required=True,
            preflight_commands=self.preflight_commands,
        )


class ExecutorAdapterEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter_id: str = Field(min_length=1)
    entrypoint: str
    smoke_argv: list[str] = Field(min_length=1)
    metrics_output: str
    allowed_paths: list[str] = Field(min_length=1)
    protected_paths: list[str] = Field(min_length=1)
    activation_evidence: Literal["observed", "inferred", "verified", "unverified"] = "unverified"
    evaluation_commands: dict[Literal["b_dev", "b_test"], ExecutorEvaluationCommand] = Field(default_factory=dict)
    preflight_required: bool = False
    preflight_commands: dict[str, ExecutorPreflightCommand] = Field(default_factory=dict)

class ExecutorAdapterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["supported", "blocked"]
    adapter_id: str | None = None
    blocker: str | None = None
    evidence: ExecutorAdapterEvidence | None = None
    source: Literal["verified_manifest", "agent_draft"] | None = None
    required_preflight_checks: list[str] = Field(default_factory=list)

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
    """Read a verified manifest or validate one agent-produced draft."""
    def inspect(self, repository_root: Path) -> ExecutorAdapterResult:
        manifest = repository_root / _CONFIG
        draft_manifest = repository_root / _DRAFT_CONFIG
        if not manifest.is_file() and draft_manifest.is_file():
            return self._inspect_draft(draft_manifest, repository_root)
        if not manifest.is_file():
            return ExecutorAdapterResult(status="blocked", blocker=f"missing explicit {_CONFIG} or {_DRAFT_CONFIG}")
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            evidence = ExecutorAdapterEvidence.model_validate(raw)
            _validate_evidence_paths(repository_root, evidence)
        except Exception as exc:
            return ExecutorAdapterResult(status="blocked", blocker=f"invalid adapter evidence: {exc}")
        return ExecutorAdapterResult(status="supported", adapter_id=evidence.adapter_id, evidence=evidence, source="verified_manifest")

    def inspect_draft(self, repository_root: Path, draft: ExecutorAdapterDraft) -> ExecutorAdapterResult:
        try:
            evidence = draft.to_evidence()
            _validate_evidence_paths(repository_root, evidence)
            _validate_draft_source_files(repository_root, draft.source_files)
            missing = [name for name in STANDARD_PREFLIGHT_CHECKS if name not in evidence.preflight_commands]
            if missing:
                raise ValueError(f"agent adapter draft is missing mandatory preflight checks: {', '.join(missing)}")
        except Exception as exc:
            return ExecutorAdapterResult(status="blocked", blocker=f"invalid adapter draft: {exc}")
        return ExecutorAdapterResult(
            status="supported",
            adapter_id=evidence.adapter_id,
            evidence=evidence,
            source="agent_draft",
            required_preflight_checks=list(STANDARD_PREFLIGHT_CHECKS),
        )

    def _inspect_draft(self, manifest: Path, repository_root: Path) -> ExecutorAdapterResult:
        try:
            draft = ExecutorAdapterDraft.model_validate_json(manifest.read_text(encoding="utf-8"))
        except Exception as exc:
            return ExecutorAdapterResult(status="blocked", blocker=f"invalid adapter draft: {exc}")
        return self.inspect_draft(repository_root, draft)

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
            args, environment, metrics_output = [evidence.entrypoint], {}, evidence.metrics_output
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
                if args.count("") != 1:
                    raise ValueError(
                        "adapter split reference binding requires exactly one explicit empty argv slot"
                    )
                args[index] = inputs.split_ref
            environment, metrics_output = phase_command.environment, phase_command.metrics_output
        plan = ExperimentCommandPlan(schema_version=1, command_id=f"{evidence.adapter_id}_{inputs.evaluation_phase}", program=inputs.python_executable, args=args, cwd=inputs.worktree_ref, environment=environment, timeout_seconds=inputs.timeout_seconds, network=False, expected_outputs=[metrics_output])
        refs = ExperimentInputRefs(repository_fingerprint=inputs.repository_fingerprint, environment_sha256=inputs.environment_sha256, dataset_manifest_sha256=inputs.dataset_manifest_sha256, asset_manifest_sha256=inputs.asset_manifest_sha256, command_sha256=experiment_command_sha256(plan))
        return plan, refs

def _safe_relative(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part == ".." for part in path.parts) or not path.parts:
        raise ValueError("adapter paths must be repository-relative")


def _validate_evidence_paths(repository_root: Path, evidence: ExecutorAdapterEvidence) -> None:
    paths = [
        evidence.entrypoint,
        evidence.metrics_output,
        *evidence.allowed_paths,
        *evidence.protected_paths,
        *[command.metrics_output for command in evidence.evaluation_commands.values()],
    ]
    for path in paths:
        _safe_relative(path)
    required_files = {evidence.entrypoint, *evidence.protected_paths}
    for path in required_files:
        if not (repository_root / path).is_file():
            raise ValueError(f"declared file is missing: {path}")
    _validate_declared_argv(evidence.smoke_argv, allow_empty_slot=False)
    for command in evidence.evaluation_commands.values():
        _validate_declared_argv(command.args, allow_empty_slot=True)
        _validate_environment(command.environment)
    for command in evidence.preflight_commands.values():
        _validate_declared_argv(command.args, allow_empty_slot=False)
        _validate_environment(command.environment)


def _validate_draft_source_files(repository_root: Path, source_files: list[str]) -> None:
    """A draft may cite only source files observed in its repository checkout."""

    for source_file in source_files:
        _safe_relative(source_file)
        if not (repository_root / source_file).is_file():
            raise ValueError(f"draft source file is missing: {source_file}")


def _validate_declared_argv(argv: list[str], *, allow_empty_slot: bool) -> None:
    """Reject command shapes that cannot be safely reproduced by the runner."""

    if not argv:
        raise ValueError("declared command argv must not be empty")
    empty_indexes: list[int] = []
    for index, value in enumerate(argv):
        if not isinstance(value, str) or (not value and not allow_empty_slot):
            raise ValueError(f"declared command argv contains an invalid value at index {index}")
        if value == "":
            empty_indexes.append(index)
        if "\x00" in value:
            raise ValueError("NUL byte forbidden in declared command argv")
        if any(token in value for token in ["|", ">", "<", "&&", "||", ";", "`", "$("]):
            raise ValueError("shell metacharacter forbidden in declared command argv")
    if empty_indexes and (not allow_empty_slot or len(empty_indexes) != 1):
        raise ValueError("declared command must contain exactly one explicit empty argv slot")


def _validate_environment(environment: dict[str, str]) -> None:
    for key, value in [*environment.items()]:
        if not key or "\x00" in key or "\x00" in value:
            raise ValueError("invalid environment entry in declared command")
