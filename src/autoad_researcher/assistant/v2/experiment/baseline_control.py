"""Server-owned baseline launch from a frozen Session contract.

The HTTP surface supplies scientific choices, never a process command, paths to
execute, or fingerprints.  This service resolves those execution details from
the Session's already-bound repository and observed environment artifacts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    metrics: list[EvaluationMetric] = Field(min_length=1)
    dataset_identity: str = Field(min_length=1)
    split_identity: str = Field(min_length=1)
    b_dev_ref: str = Field(min_length=1)
    b_test_ref: str = Field(min_length=1)
    category_set: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    seeds: list[int] = Field(min_length=1)
    checkpoint_selection: str = Field(min_length=1)
    max_wall_seconds: int = Field(gt=0)
    max_gpu_seconds: int = Field(ge=0)
    required_device_count: int = Field(default=0, ge=0)
    required_vram_mb: int = Field(default=0, ge=0)
    dataset_source_ids: list[str] = Field(default_factory=list)
    asset_source_ids: list[str] = Field(default_factory=list)

    @field_validator("b_dev_ref", "b_test_ref")
    @classmethod
    def _relative_ref(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            raise ValueError("contract references must be run-relative")
        return value

    @model_validator(mode="after")
    def _validate_metrics(self):
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("baseline metrics must be unique")
        if self.primary_metric not in names:
            raise ValueError("primary_metric must name one of metrics")
        if not set(self.guardrails).issubset(names):
            raise ValueError("guardrails must name metrics")
        if self.primary_metric in self.guardrails:
            raise ValueError("primary_metric cannot also be a guardrail")
        if self.required_device_count == 0 and self.required_vram_mb != 0:
            raise ValueError("required_vram_mb requires a positive device request")
        if self.max_gpu_seconds == 0 and self.required_device_count != 0:
            raise ValueError("CPU-only baseline cannot request GPU devices")
        if self.max_gpu_seconds > 0 and self.required_device_count == 0:
            raise ValueError("GPU time budget requires an explicit device request")
        return self


class HeldOutAuthorization(BaseModel):
    """Durable approval binding one candidate to one held-out comparison."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    confirmation_id: str = Field(pattern=r"^heldout_[0-9a-f]{16}$")
    session_id: str = Field(min_length=1)
    candidate_attempt_id: str = Field(pattern=r"^attempt_[0-9]{6}$")
    noise_threshold: float = Field(ge=0)
    idempotency_key: str = Field(min_length=1)
    evaluation_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_branch: str = Field(min_length=1)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    baseline_b_test_attempt_id: str | None = Field(default=None, pattern=r"^attempt_[0-9]{6}$")
    candidate_b_test_attempt_id: str | None = Field(default=None, pattern=r"^attempt_[0-9]{6}$")
    status: Literal["authorized", "paired", "completed"] = "authorized"
    created_at: str = Field(min_length=1)


HELD_OUT_AUTHORIZATIONS_DIR = "experiments/held_out_confirmations"


def held_out_authorization_path(run_dir: Path, confirmation_id: str) -> Path:
    if not confirmation_id.startswith("heldout_"):
        raise ValueError("invalid held-out confirmation id")
    return run_dir / HELD_OUT_AUTHORIZATIONS_DIR / f"{confirmation_id}.json"


def load_held_out_authorization(run_dir: Path, confirmation_id: str) -> HeldOutAuthorization | None:
    path = held_out_authorization_path(run_dir, confirmation_id)
    if not path.is_file():
        return None
    return HeldOutAuthorization.model_validate_json(path.read_text(encoding="utf-8"))


def list_held_out_authorizations(run_dir: Path) -> list[HeldOutAuthorization]:
    directory = run_dir / HELD_OUT_AUTHORIZATIONS_DIR
    if not directory.is_dir():
        return []
    return [
        HeldOutAuthorization.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("heldout_*.json"))
    ]


def save_held_out_authorization(run_dir: Path, value: HeldOutAuthorization) -> HeldOutAuthorization:
    path = held_out_authorization_path(run_dir, value.confirmation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        existing = HeldOutAuthorization.model_validate_json(path.read_text(encoding="utf-8"))
        immutable = ("session_id", "candidate_attempt_id", "noise_threshold", "idempotency_key", "evaluation_contract_sha256", "source_branch", "source_commit", "created_at")
        if any(getattr(existing, key) != getattr(value, key) for key in immutable):
            raise ValueError("idempotency_conflict: held-out authorization differs")
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(value.model_dump_json(indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return value


class BaselineLaunchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started: ExperimentAttemptStartResult
    b_test_started: ExperimentAttemptStartResult | None = None
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
            self._require_matching_replay(run_dir, session_id=session_id, value=contract_input)
            job = self._pipeline_job(run_dir, existing.pipeline_job_id)
            return BaselineLaunchResult(
                started=ExperimentAttemptStartResult(attempt=existing, pipeline_job=job, disposition="reused"),
                evaluation_contract_ref=session.evaluation_contract_ref,
                execution_inputs_ref=f"experiments/execution_inputs/{session_id}.json",
            )

        binding = self._load_binding(run_dir, session)
        snapshot, python_executable = self._load_observed_environment(run_dir, session)
        task = self._load_input_task(run_dir, session)
        task_metric_names = set(task.primary_metrics)
        contract_metric_names = {metric.name for metric in contract_input.metrics}
        if contract_metric_names != task_metric_names:
            raise ValueError("baseline metrics must exactly match the user-confirmed task metrics")
        if contract_input.primary_metric not in task_metric_names:
            raise ValueError("baseline primary metric must be one of the user-confirmed task metrics")
        split_paths = self._require_input_files(run_dir, contract_input)
        selected_sources = self._freeze_selected_sources(
            run_dir,
            contract_input.dataset_source_ids + contract_input.asset_source_ids,
        )

        adapter_protected_paths = self._adapter_protected_paths(run_dir, binding)
        implementation_paths = [metric.implementation_ref for metric in contract_input.metrics]
        workspace_protected_paths = list(dict.fromkeys([*adapter_protected_paths, *implementation_paths]))
        workspace_key = f"baseline-{session_id}"
        workspace = WorktreeManager(run_dir / "experiments" / "executor_worktrees").create(
            repository_path=run_dir / binding.repository_ref,
            attempt_id=workspace_key,
            base_commit="HEAD",
            protected_paths=workspace_protected_paths,
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
        inputs_ref, dataset_sha, asset_sha = self._write_execution_inputs(
            run_dir, session_id, contract_input, selected_sources=selected_sources,
        )
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
                evaluation_phase="b_dev",
                split_ref=str(split_paths[0]),
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
            required_device_count=contract_input.required_device_count,
            required_vram_mb=contract_input.required_vram_mb,
            evaluation_contract_ref=frozen.ref,
            evaluation_contract_sha256=frozen.sha256,
            protected_artifact_report_ref=protected_ref,
            protected_artifact_report_sha256=protected_sha,
        )
        self._sessions.update_baseline_state(run_dir, session_id=session_id, status="BASELINE_RUNNING", baseline_status="queued")
        return BaselineLaunchResult(
            started=started,
            evaluation_contract_ref=frozen.ref,
            execution_inputs_ref=inputs_ref,
        )

    def start_b_test(
        self,
        run_dir: Path,
        *,
        session_id: str,
        confirmation_id: str | None = None,
    ) -> BaselineLaunchResult:
        """Queue Baseline B_test only for a persisted Candidate approval."""
        if confirmation_id is None:
            raise ValueError("held_out_confirmation_required: Candidate approval must precede B_test")
        authorization = load_held_out_authorization(run_dir, confirmation_id)
        if authorization is None or authorization.session_id != session_id:
            raise ValueError("held_out_confirmation_required: held-out authorization is missing")
        if authorization.status not in {"authorized", "paired", "completed"}:
            raise ValueError("held_out_confirmation_required: held-out authorization is invalid")
        session = self._sessions.load(run_dir, session_id)
        if session is None:
            raise FileNotFoundError("experiment session not found")
        if session.evaluation_contract_sha256 != authorization.evaluation_contract_sha256:
            raise ValueError("held_out_confirmation_required: authorization contract differs from Session")
        attempts = self._attempt_store.list_for_session(run_dir, session_id=session_id)
        baseline = next((item for item in attempts if item.job_type == "experiment_baseline"), None)
        if baseline is None:
            raise ValueError("baseline B_test requires a completed B_dev Attempt")
        existing = next((item for item in attempts if item.job_type == "experiment_baseline_b_test"), None)
        if existing is not None:
            if authorization.baseline_b_test_attempt_id not in {None, existing.attempt_id}:
                raise ValueError("idempotency_conflict: held-out authorization points to another baseline B_test")
            if session.evaluation_contract_ref is None:
                raise ValueError("existing baseline B_test is missing its Session evaluation contract")
            return BaselineLaunchResult(
                started=ExperimentAttemptStartResult(
                    attempt=baseline,
                    pipeline_job=self._pipeline_job(run_dir, baseline.pipeline_job_id),
                    disposition="reused",
                ),
                b_test_started=ExperimentAttemptStartResult(
                    attempt=existing,
                    pipeline_job=self._pipeline_job(run_dir, existing.pipeline_job_id),
                    disposition="reused",
                ),
                evaluation_contract_ref=session.evaluation_contract_ref,
                execution_inputs_ref=f"experiments/execution_inputs/{session_id}.json",
            )
        if session.status != "READY_FOR_BASELINE" or session.baseline_status != "b_dev_completed":
            raise ValueError("baseline B_test requires Session READY_FOR_BASELINE after B_dev")
        if baseline.runtime_status != "COMPLETED":
            raise ValueError("baseline B_test requires a completed B_dev Attempt")
        if not session.evaluation_contract_ref or not session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: Session evaluation contract is missing")

        frozen = self._contracts.current(run_dir, session_id=session_id)
        if frozen is None or frozen.ref != session.evaluation_contract_ref or frozen.sha256 != session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: Session evaluation contract changed")
        contract = frozen.contract
        split_path = resolve_split_artifact(run_dir, contract.b_test_ref)
        workspace_path = _resolve_run_relative_path(run_dir, baseline.command_plan.cwd)
        adapter_result = ExecutorAdapter().inspect(workspace_path)
        if adapter_result.status != "supported" or adapter_result.evidence is None:
            raise ValueError(adapter_result.blocker or "execution adapter is unsupported")
        if "b_test" not in adapter_result.evidence.evaluation_commands:
            raise ValueError("adapter has no explicit b_test command for the frozen split")
        plan, refs = ExecutorAdapter().build_execution(
            adapter_result,
            ExecutorAdapterInputs(
                run_id=run_dir.name,
                worktree_ref=baseline.command_plan.cwd,
                repository_fingerprint=baseline.input_refs.repository_fingerprint,
                environment_sha256=baseline.input_refs.environment_sha256,
                dataset_manifest_sha256=baseline.input_refs.dataset_manifest_sha256,
                asset_manifest_sha256=baseline.input_refs.asset_manifest_sha256,
                python_executable=baseline.command_plan.program,
                timeout_seconds=baseline.job_timeout_sec,
                evaluation_phase="b_test",
                split_ref=str(split_path),
            ),
        )
        started = self._attempts.create_or_get_attempt(
            run_dir,
            session_id=session_id,
            job_type="experiment_baseline_b_test",
            idempotency_key=f"baseline-b-test:{session_id}:{frozen.sha256}",
            command_plan=plan,
            input_refs=refs,
            job_timeout_sec=baseline.job_timeout_sec,
            required_device_count=baseline.required_device_count,
            required_vram_mb=baseline.required_vram_mb,
            evaluation_contract_ref=frozen.ref,
            evaluation_contract_sha256=frozen.sha256,
            protected_artifact_report_ref=baseline.protected_artifact_report_ref,
            protected_artifact_report_sha256=baseline.protected_artifact_report_sha256,
        )
        self._sessions.update_baseline_state(
            run_dir,
            session_id=session_id,
            status="BASELINE_RUNNING",
            baseline_status="queued",
        )
        return BaselineLaunchResult(
            started=ExperimentAttemptStartResult(
                attempt=baseline,
                pipeline_job=self._pipeline_job(run_dir, baseline.pipeline_job_id),
                disposition="reused",
            ),
            b_test_started=started,
            evaluation_contract_ref=frozen.ref,
            execution_inputs_ref=f"experiments/execution_inputs/{session_id}.json",
        )

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
    def _require_input_files(run_dir: Path, value: BaselineContractInput) -> tuple[Path, Path]:
        return resolve_split_artifact(run_dir, value.b_dev_ref), resolve_split_artifact(run_dir, value.b_test_ref)

    @staticmethod
    def _freeze_selected_sources(run_dir: Path, source_ids: list[str]) -> list[dict[str, str]]:
        """Freeze only explicitly selected, fully acquired run-local materials."""
        from autoad_researcher.ui.sources import load_source_registry

        known = {
            str(item.get("source_id")): item
            for item in load_source_registry(run_dir).get("sources", [])
            if isinstance(item, dict) and isinstance(item.get("source_id"), str)
        }
        frozen: list[dict[str, str]] = []
        for source_id in sorted(set(source_ids)):
            source = known.get(source_id)
            if source is None:
                raise ValueError("execution_contract_incomplete: selected input source is not registered")
            if source.get("intake_status") != "ok":
                raise ValueError("execution_contract_incomplete: selected input source is not acquired")
            ref = source.get("stored_path")
            if not isinstance(ref, str) or not ref:
                raise ValueError("execution_contract_incomplete: selected input source has no frozen artifact")
            path = run_dir.joinpath(*PurePosixPath(ref).parts).resolve()
            if not path.is_relative_to(run_dir.resolve()) or not path.is_file():
                raise ValueError("execution_contract_incomplete: selected input artifact is missing")
            frozen.append({"source_id": source_id, "artifact_ref": ref, "artifact_sha256": sha256_file(path)})
        return frozen

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
            metrics=value.metrics,
            primary_metric=value.primary_metric,
            guardrails=value.guardrails,
            aggregation="mean",
            seeds=value.seeds,
            checkpoint_selection=value.checkpoint_selection,
            resource_budget=EvaluationResourceBudget(max_wall_seconds=value.max_wall_seconds, max_gpu_seconds=value.max_gpu_seconds),
            required_device_count=value.required_device_count,
            required_vram_mb=value.required_vram_mb,
            protected_paths=list(dict.fromkeys([
                value.b_dev_ref,
                value.b_test_ref,
                *[
                    str(PurePosixPath(workspace_ref) / path)
                    for path in [*protected_paths, *(metric.implementation_ref for metric in value.metrics)]
                ],
            ])),
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
    def _write_execution_inputs(
        run_dir: Path,
        session_id: str,
        value: BaselineContractInput,
        *,
        selected_sources: list[dict[str, str]],
    ) -> tuple[str, str, str]:
        payload = {
            "schema_version": 1,
            "session_id": session_id,
            "dataset_identity": value.dataset_identity,
            "split_identity": value.split_identity,
            "dataset_source_ids": value.dataset_source_ids,
            "asset_source_ids": value.asset_source_ids,
            "selected_source_artifacts": selected_sources,
            "split_artifacts": [
                {"artifact_ref": value.b_dev_ref, "artifact_sha256": sha256_file(run_dir / value.b_dev_ref)},
                {"artifact_ref": value.b_test_ref, "artifact_sha256": sha256_file(run_dir / value.b_test_ref)},
            ],
            "baseline_request_sha256": canonical_sha256(value),
        }
        dataset_sources = [item for item in selected_sources if item["source_id"] in value.dataset_source_ids]
        asset_sources = [item for item in selected_sources if item["source_id"] in value.asset_source_ids]
        dataset_sha = canonical_sha256(
            {**{key: payload[key] for key in ["dataset_identity", "split_identity", "split_artifacts"]}, "sources": dataset_sources}
        )
        asset_sha = canonical_sha256({"sources": asset_sources})
        payload["dataset_manifest_sha256"] = dataset_sha
        payload["asset_manifest_sha256"] = asset_sha
        path = run_dir / "experiments" / "execution_inputs" / f"{session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return str(path.relative_to(run_dir)), dataset_sha, asset_sha

    @staticmethod
    def _require_matching_replay(run_dir: Path, *, session_id: str, value: BaselineContractInput) -> None:
        """Reuse only an exactly identical baseline launch request.

        A pre-receipt artifact predates this invariant and cannot establish that
        the caller requested the same protocol, so it deliberately fails closed.
        """

        path = run_dir / "experiments" / "execution_inputs" / f"{session_id}.json"
        if not path.is_file():
            raise ValueError("idempotency_conflict: existing baseline request receipt is missing")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            recorded = payload.get("baseline_request_sha256")
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("idempotency_conflict: existing baseline request receipt is invalid") from exc
        if recorded != canonical_sha256(value):
            raise ValueError("idempotency_conflict: baseline request differs from the existing Attempt")


def resolve_split_artifact(run_dir: Path, ref: str) -> Path:
    path = run_dir.joinpath(*PurePosixPath(ref).parts).resolve()
    if not path.is_relative_to(run_dir.resolve()) or not path.is_file():
        raise ValueError("execution_contract_incomplete: frozen split artifact is missing")
    return path


def _resolve_run_relative_path(run_dir: Path, relative_path: str) -> Path:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("baseline workspace reference must stay within the run directory")
    resolved = run_dir.joinpath(*path.parts).resolve()
    if not resolved.is_relative_to(run_dir.resolve()) or not resolved.is_dir():
        raise ValueError("baseline workspace reference is missing")
    return resolved
