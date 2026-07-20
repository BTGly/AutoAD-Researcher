"""Server-owned baseline launch from a frozen Session contract.

The HTTP surface supplies scientific choices, never a process command, paths to
execute, or fingerprints.  This service resolves those execution details from
the Session's already-bound repository and observed environment artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.assistant.v2.execution_repository import ExecutionRepositoryBinding
from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.environments.context_collector import CollectedValidationContext
from autoad_researcher.environments.snapshot import EnvironmentSnapshot
from autoad_researcher.experiment.attempt_service import ExperimentAttemptService, ExperimentAttemptStartResult
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.evaluation_contract import (
    EvaluationContract,
    EvaluationContractStore,
    EvaluationMetric,
    EvaluationResourceBudget,
    freeze_protected_artifacts,
)
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter, ExecutorAdapterInputs
from autoad_researcher.experiment.finalizer import ProtectedArtifactHashes
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.experiment.worktree import WorktreeManager
from autoad_researcher.schemas.intake import InputTask


class BaselineContractInput(BaseModel):
    """The user-confirmed scientific choices that are not safely inferable."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    primary_metric: str = Field(min_length=1)
    primary_metric_direction: Literal["maximize", "minimize"]
    dataset_identity: str = Field(min_length=1)
    split_identity: str = Field(min_length=1)
    b_dev_ref: str = Field(min_length=1)
    b_test_ref: str = Field(min_length=1)
    category_set: list[str] = Field(min_length=1)
    seeds: list[int] = Field(min_length=1)
    checkpoint_selection: str = Field(min_length=1)
    max_wall_seconds: int = Field(gt=0)
    max_gpu_seconds: int = Field(gt=0)
    dataset_source_ids: list[str] = Field(default_factory=list)
    asset_source_ids: list[str] = Field(default_factory=list)

    @field_validator("b_dev_ref", "b_test_ref")
    @classmethod
    def _relative_ref(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            raise ValueError("contract references must be run-relative")
        return value


class BaselineLaunchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started: ExperimentAttemptStartResult
    evaluation_contract_ref: str
    execution_inputs_ref: str


class BaselineControlService:
    """Freeze one baseline protocol and enqueue its immutable Attempt."""

    def __init__(self):
        self._sessions = ExperimentSessionStore()
        self._attempts = ExperimentAttemptService()
        self._attempt_store = ExperimentAttemptStore()
        self._contracts = EvaluationContractStore()

    def start(self, run_dir: Path, *, session_id: str, contract_input: BaselineContractInput) -> BaselineLaunchResult:
        session = self._require_ready_session(run_dir, session_id)
        existing = next(
            (
                attempt
                for attempt in self._attempt_store.list_for_session(run_dir, session_id=session_id)
                if attempt.job_type == "experiment_baseline"
            ),
            None,
        )
        if existing is not None:
            if session.evaluation_contract_ref is None:
                raise ValueError("existing baseline is missing its Session evaluation contract")
            job = self._pipeline_job(run_dir, existing.pipeline_job_id)
            return BaselineLaunchResult(
                started=ExperimentAttemptStartResult(attempt=existing, pipeline_job=job, disposition="reused"),
                evaluation_contract_ref=session.evaluation_contract_ref,
                execution_inputs_ref=f"experiments/execution_inputs/{session_id}.json",
            )

        binding = self._load_binding(run_dir, session)
        snapshot, python_executable = self._load_observed_environment(run_dir, session)
        task = self._load_input_task(run_dir, session)
        if contract_input.primary_metric not in task.primary_metrics:
            raise ValueError("baseline primary metric must be one of the user-confirmed task metrics")
        self._require_confirmed_sources(run_dir, contract_input.dataset_source_ids + contract_input.asset_source_ids)

        workspace_key = f"baseline-{session_id}"
        workspace = WorktreeManager(run_dir / "experiments" / "executor_worktrees").create(
            repository_path=run_dir / binding.repository_ref,
            attempt_id=workspace_key,
            base_commit="HEAD",
            protected_paths=self._adapter_protected_paths(run_dir, binding),
            environment_snapshot_ref=session.environment_snapshot_ref or "",
        )
        workspace_ref = str(Path(workspace.worktree_path).resolve().relative_to(run_dir.resolve()))
        adapter = ExecutorAdapter()
        adapter_result = adapter.inspect(Path(workspace.worktree_path))
        if adapter_result.status != "supported" or adapter_result.evidence is None:
            raise ValueError(adapter_result.blocker or "execution adapter is unsupported")
        if adapter_result.adapter_id != binding.adapter_id:
            raise ValueError("execution adapter differs from the frozen repository binding")

        frozen = self._freeze_contract(
            run_dir,
            session_id=session_id,
            workspace_ref=workspace_ref,
            base_commit=workspace.base_commit,
            protected_paths=adapter_result.evidence.protected_paths,
            value=contract_input,
        )
        self._sessions.bind_evaluation_contract(
            run_dir,
            session_id=session_id,
            evaluation_contract_ref=frozen.ref,
            evaluation_contract_sha256=frozen.sha256,
            evaluation_contract_revision=frozen.contract.revision,
        )
        protected_ref, protected_sha = self._freeze_protected_artifacts(run_dir, frozen.contract, session_id)
        inputs_ref, dataset_sha, asset_sha = self._write_execution_inputs(run_dir, session_id, contract_input)
        plan, refs = adapter.build_execution(
            adapter_result,
            ExecutorAdapterInputs(
                run_id=run_dir.name,
                worktree_ref=workspace_ref,
                repository_fingerprint=binding.repository_fingerprint,
                environment_sha256=snapshot.environment_sha256,
                dataset_manifest_sha256=dataset_sha,
                asset_manifest_sha256=asset_sha,
                python_executable=python_executable,
                timeout_seconds=contract_input.max_wall_seconds,
            ),
        )
        started = self._attempts.create_or_get_attempt(
            run_dir,
            session_id=session_id,
            job_type="experiment_baseline",
            idempotency_key=f"baseline:{session_id}:{frozen.sha256}",
            command_plan=plan,
            input_refs=refs,
            job_timeout_sec=contract_input.max_wall_seconds,
            evaluation_contract_ref=frozen.ref,
            evaluation_contract_sha256=frozen.sha256,
            protected_artifact_report_ref=protected_ref,
            protected_artifact_report_sha256=protected_sha,
        )
        self._sessions.update_baseline_state(run_dir, session_id=session_id, status="BASELINE_RUNNING", baseline_status="queued")
        return BaselineLaunchResult(started=started, evaluation_contract_ref=frozen.ref, execution_inputs_ref=inputs_ref)

    def _require_ready_session(self, run_dir: Path, session_id: str):
        session = self._sessions.load(run_dir, session_id)
        if session is None:
            raise FileNotFoundError("experiment session not found")
        if session.status not in {"READY_FOR_BASELINE", "BASELINE_RUNNING", "READY"}:
            raise ValueError("baseline launch requires an environment-ready Session")
        if session.authorization.execution_mode == "plan_only":
            raise ValueError("plan_only Session may not launch a baseline")
        if not session.environment_snapshot_ref:
            raise ValueError("execution_contract_incomplete: observed environment snapshot is missing")
        return session

    @staticmethod
    def _pipeline_job(run_dir: Path, job_id: str | None) -> dict[str, object]:
        from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs

        job = next((item for item in load_pipeline_jobs(run_dir) if item.get("job_id") == job_id), None)
        if job is None:
            raise FileNotFoundError("baseline PipelineJob is missing")
        return job

    @staticmethod
    def _load_input_task(run_dir: Path, session) -> InputTask:
        path = run_dir / session.task_ref
        if not path.is_file():
            raise ValueError("execution_contract_incomplete: confirmed input task is missing")
        return InputTask.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    @staticmethod
    def _load_binding(run_dir: Path, session) -> ExecutionRepositoryBinding:
        if not session.execution_repository_binding_ref or not session.execution_repository_binding_sha256:
            raise ValueError("execution_contract_incomplete: execution repository binding is missing")
        path = run_dir / session.execution_repository_binding_ref
        if not path.is_file():
            raise ValueError("execution_contract_incomplete: execution repository binding changed")
        binding = ExecutionRepositoryBinding.model_validate_json(path.read_text(encoding="utf-8"))
        if canonical_sha256(binding) != session.execution_repository_binding_sha256:
            raise ValueError("execution_contract_incomplete: execution repository binding changed")
        if session.repository_ref != binding.repository_ref:
            raise ValueError("execution repository differs from the frozen binding")
        return binding

    @staticmethod
    def _load_observed_environment(run_dir: Path, session) -> tuple[EnvironmentSnapshot, str]:
        snapshot_path = run_dir / str(session.environment_snapshot_ref)
        if not snapshot_path.is_file():
            raise ValueError("execution_contract_incomplete: observed environment snapshot is missing")
        snapshot = EnvironmentSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
        context_path = run_dir / "environment" / f"validation_context_r{session.environment_revision}.json"
        if not context_path.is_file():
            raise ValueError("execution_contract_incomplete: observed Python executable is missing")
        context = CollectedValidationContext.model_validate_json(context_path.read_text(encoding="utf-8"))
        return snapshot, context.python_executable

    @staticmethod
    def _adapter_protected_paths(run_dir: Path, binding: ExecutionRepositoryBinding) -> list[str]:
        path = run_dir / binding.adapter_manifest_ref
        if not path.is_file() or sha256_file(path) != binding.adapter_manifest_sha256:
            raise ValueError("execution_contract_incomplete: adapter manifest changed")
        raw = json.loads(path.read_text(encoding="utf-8"))
        protected = raw.get("protected_paths") if isinstance(raw, dict) else None
        if not isinstance(protected, list) or not all(isinstance(item, str) and item for item in protected):
            raise ValueError("execution_contract_incomplete: adapter protected paths are invalid")
        return protected

    @staticmethod
    def _require_confirmed_sources(run_dir: Path, source_ids: list[str]) -> None:
        from autoad_researcher.ui.sources import load_source_registry

        known = {str(item.get("source_id")) for item in load_source_registry(run_dir).get("sources", []) if isinstance(item, dict)}
        missing = sorted(set(source_ids) - known)
        if missing:
            raise ValueError("execution_contract_incomplete: selected input source is not registered")

    def _freeze_contract(self, run_dir: Path, *, session_id: str, workspace_ref: str, base_commit: str, protected_paths: list[str], value: BaselineContractInput):
        current = self._contracts.current(run_dir, session_id=session_id)
        contract = EvaluationContract(
            contract_id="evaluation_contract_000001",
            session_id=session_id,
            revision=0,
            baseline_commit=base_commit,
            dataset_identity=value.dataset_identity,
            split_identity=value.split_identity,
            b_dev_ref=value.b_dev_ref,
            b_test_ref=value.b_test_ref,
            category_set=value.category_set,
            metrics=[EvaluationMetric(name=value.primary_metric, direction=value.primary_metric_direction, implementation_ref="metrics.json")],
            primary_metric=value.primary_metric,
            aggregation="mean",
            seeds=value.seeds,
            checkpoint_selection=value.checkpoint_selection,
            resource_budget=EvaluationResourceBudget(max_wall_seconds=value.max_wall_seconds, max_gpu_seconds=value.max_gpu_seconds),
            protected_paths=[f"{workspace_ref}/{path}" for path in protected_paths],
        )
        if current is not None:
            if current.contract != contract:
                raise ValueError("baseline evaluation contract is already frozen differently")
            return current
        return self._contracts.freeze(run_dir, contract=contract)

    @staticmethod
    def _freeze_protected_artifacts(run_dir: Path, contract: EvaluationContract, session_id: str) -> tuple[str, str]:
        model = ProtectedArtifactHashes(hashes=freeze_protected_artifacts(run_dir, contract.protected_paths))
        path = run_dir / "experiments" / "protected_artifacts" / f"{session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return str(path.relative_to(run_dir)), sha256_file(path)

    @staticmethod
    def _write_execution_inputs(run_dir: Path, session_id: str, value: BaselineContractInput) -> tuple[str, str, str]:
        payload = {
            "schema_version": 1,
            "session_id": session_id,
            "dataset_identity": value.dataset_identity,
            "split_identity": value.split_identity,
            "dataset_source_ids": value.dataset_source_ids,
            "asset_source_ids": value.asset_source_ids,
        }
        dataset_sha = canonical_sha256({key: payload[key] for key in ["dataset_identity", "split_identity", "dataset_source_ids"]})
        asset_sha = canonical_sha256({"asset_source_ids": value.asset_source_ids})
        payload["dataset_manifest_sha256"] = dataset_sha
        payload["asset_manifest_sha256"] = asset_sha
        path = run_dir / "experiments" / "execution_inputs" / f"{session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return str(path.relative_to(run_dir)), dataset_sha, asset_sha
