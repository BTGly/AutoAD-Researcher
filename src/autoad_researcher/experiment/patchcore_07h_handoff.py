"""07H-specific worktree patch -> admitted PatchCore command -> Attempt handoff."""

from __future__ import annotations

import json
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.config import InternalBenchmarkCase
from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.benchmarks.patchcore_07h_executor_adapter import PatchCore07HAdapterInputs, PatchCore07HExecutorAdapter
from autoad_researcher.experiment.attempt_service import ExperimentAttemptService
from autoad_researcher.experiment.executor_agent import ExecutorAgent, ExecutorLimits, ExecutorSummary
from autoad_researcher.experiment.executor_contracts import InterventionContract, WorkspaceSpec
from autoad_researcher.experiment.intervention_admission import InterventionAdmissionService
from autoad_researcher.experiment.scientific_assessment import ScientificAssessmentInputsStore, ScientificEvaluationInputs
from autoad_researcher.experiment.validity import ComparisonIdentity
from autoad_researcher.experiment.worktree import WorktreeManager
from autoad_researcher.runner import ExperimentInputRefs


INTERVENTION_CONFIG = ".autoad/patchcore_07h_intervention.json"


class PatchCore07HInterventionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    idempotency_key: str
    repository_path: Path
    base_commit: str
    environment_snapshot_ref: str
    case_path: Path
    benchmark_python: Path
    dataset_path: Path
    weight_path: Path
    environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_fingerprint: str = Field(pattern=r"^[0-9a-f]{40}$")
    intervention_contract: InterventionContract
    evaluation_contract_ref: str
    evaluation_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_artifact_report_ref: str
    protected_artifact_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_metrics: dict[str, float]


class PatchCore07HInterventionHandoff:
    def __init__(self, *, attempts: ExperimentAttemptService | None = None, admission: InterventionAdmissionService | None = None):
        self._attempts = attempts or ExperimentAttemptService()
        self._admission = admission or InterventionAdmissionService()

    def handoff(self, run_dir: Path, *, request: PatchCore07HInterventionRequest, proposal_provider: Callable) -> tuple[dict, dict, WorkspaceSpec]:
        case = InternalBenchmarkCase.model_validate_json(request.case_path.with_suffix(".json").read_text(encoding="utf-8")) if request.case_path.suffix == ".json" else _load_case(request.case_path)
        key = sha256(request.idempotency_key.encode()).hexdigest()[:16]
        workspace = WorktreeManager(run_dir / "executor_worktrees").create(
            repository_path=request.repository_path, attempt_id=key, base_commit=request.base_commit,
            protected_paths=case.evaluation.protected_paths, environment_snapshot_ref=request.environment_snapshot_ref,
        )
        self._bootstrap_config(Path(workspace.worktree_path), case, request.intervention_contract)
        staging = run_dir / "executor_staging" / key
        summary_path = staging / "executor_summary.json"
        admission_path = staging / "intervention_admission.json"
        summary = (
            ExecutorSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
            if summary_path.is_file() and admission_path.is_file()
            else ExecutorAgent(
                contract=request.intervention_contract, workspace=workspace, artifact_dir=staging,
                limits=ExecutorLimits(max_steps=8, max_wall_seconds=request.intervention_contract.time_budget, max_model_calls=request.intervention_contract.max_repairs + 1),
            ).run(proposal_provider)
        )
        if summary.status != "completed":
            raise ValueError(summary.error or "Executor did not complete")
        overrides = _load_overrides(Path(workspace.worktree_path) / INTERVENTION_CONFIG)
        plan, refs = PatchCore07HExecutorAdapter(case=case).build(PatchCore07HAdapterInputs(
            run_id=run_dir.name, attempt_id=key, repository=Path(workspace.worktree_path), benchmark_python=request.benchmark_python,
            dataset_path=request.dataset_path, weight_path=request.weight_path, environment_sha256=request.environment_sha256,
            dataset_manifest_sha256=request.dataset_manifest_sha256, asset_manifest_sha256=request.asset_manifest_sha256,
            repository_fingerprint=request.repository_fingerprint, allowed_parameters=_allowed_parameters(request.intervention_contract),
            parameter_overrides=overrides, artifact_dir=staging,
        ))
        decision = self._admission.admit(contract=request.intervention_contract, workspace=workspace, summary=summary, artifact_dir=staging, command_plan=plan)
        if not decision.allowed:
            raise ValueError(f"{decision.code}: {decision.detail}")
        started = self._attempts.create_or_get_attempt(
            run_dir, session_id=request.session_id, job_type="experiment_attempt", idempotency_key=request.idempotency_key,
            command_plan=plan, input_refs=refs, job_timeout_sec=1800, required_device_count=1, required_vram_mb=20000,
            evaluation_contract_ref=request.evaluation_contract_ref, evaluation_contract_sha256=request.evaluation_contract_sha256,
            protected_artifact_report_ref=request.protected_artifact_report_ref, protected_artifact_report_sha256=request.protected_artifact_report_sha256,
        )
        artifact_dir = run_dir / "attempts" / started.attempt.attempt_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        identity = ComparisonIdentity(
            dataset_identity=f"07h-b-dev:{request.dataset_manifest_sha256}",
            split_identity=request.dataset_manifest_sha256,
            seed=0,
            checkpoint_selection="not_applicable",
            command_sha256=refs.command_sha256,
            metric_implementation_refs=case.evaluation.evaluator_paths,
            evaluation_contract_sha256=request.evaluation_contract_sha256,
            outputs_complete=True,
        )
        ScientificAssessmentInputsStore().save(
            artifact_dir,
            ScientificEvaluationInputs(
                baseline_metrics=request.baseline_metrics,
                candidate_identity=identity,
                baseline_identity=identity,
            ),
        )
        for name in ["patch.diff", "final_patch.diff", "intervention_admission.json", "executor_summary.json", "patchcore_command.json"]:
            source = staging / name
            if source.is_file(): shutil.copy2(source, artifact_dir / name)
        (artifact_dir / "intervention_contract.json").write_text(request.intervention_contract.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (artifact_dir / "workspace.json").write_text(workspace.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return started.attempt.model_dump(mode="json"), started.pipeline_job, workspace

    @staticmethod
    def _bootstrap_config(worktree: Path, case: InternalBenchmarkCase, contract: InterventionContract) -> None:
        path = worktree / INTERVENTION_CONFIG
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        allowed = _allowed_parameters(contract)
        if len(allowed) != 1 or allowed[0] not in case.fixed_parameters:
            raise ValueError("07H intervention contract must authorize exactly one fixed PatchCore parameter")
        parameter = allowed[0]
        path.write_text(json.dumps({"parameter_overrides": {parameter: case.fixed_parameters[parameter]}}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "-N", INTERVENTION_CONFIG], cwd=worktree, check=True, capture_output=True, text=True, shell=False)


def _load_case(path: Path) -> InternalBenchmarkCase:
    from autoad_researcher.benchmarks.config import load_internal_benchmark_case
    return load_internal_benchmark_case(path)


def _load_overrides(path: Path) -> dict[str, int | float]:
    value = json.loads(path.read_text(encoding="utf-8"))
    overrides = value.get("parameter_overrides") if isinstance(value, dict) else None
    if not isinstance(overrides, dict):
        raise ValueError("intervention config has no parameter_overrides object")
    return {str(key): raw for key, raw in overrides.items() if isinstance(raw, (int, float)) and not isinstance(raw, bool)}


def _allowed_parameters(contract: InterventionContract) -> list[str]:
    values = contract.allowed_parameters if isinstance(contract.allowed_parameters, list) else list(contract.allowed_parameters)
    return [str(value) for value in values]
