"""Controlled model-code repair for a failed baseline Attempt."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.experiment.baseline_control import (
    BaselineControlService,
    resolve_split_artifact,
)
from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.evaluation_contract import EvaluationContract
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter, ExecutorAdapterInputs
from autoad_researcher.experiment.executor_agent import ExecutorProposal
from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.experiment.executor_handoff import ExecutorAttemptHandoffService, ExecutorHandoffRequest
from autoad_researcher.experiment.session_store import ExperimentSessionStore


class BaselineRepairInput(BaseModel):
    """A user-confirmed, bounded repair; it never contains a raw command."""

    model_config = ConfigDict(extra="forbid")

    failed_attempt_id: str = Field(pattern=r"^attempt_[0-9]{6}$")
    intervention_contract: InterventionContract
    approved_proposal: ExecutorProposal
    idempotency_key: str = Field(min_length=1)


class BaselineRepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    attempt: dict | None = None
    pipeline_job: dict | None = None
    workspace_ref: str | None = None
    blocker: str | None = None


class BaselineRepairService:
    """Reuse the existing Executor/Attempt control plane for baseline repair."""

    def __init__(self) -> None:
        self._sessions = ExperimentSessionStore()
        self._attempts = ExperimentAttemptStore()

    def start(self, run_dir: Path, *, session_id: str, value: BaselineRepairInput) -> BaselineRepairResult:
        session = self._sessions.load(run_dir, session_id)
        if session is None:
            raise FileNotFoundError("experiment session not found")
        failed = self._attempts.load(run_dir, value.failed_attempt_id)
        if failed is None or failed.session_id != session_id:
            raise ValueError("baseline repair must bind a failed Attempt in the source Session")
        if failed.job_type != "experiment_baseline" or failed.attempt_purpose not in {"baseline", "repair"}:
            raise ValueError("baseline repair target must be a baseline Attempt")
        if failed.runtime_status not in {"FAILED", "TIMED_OUT", "LOST"}:
            raise ValueError("baseline repair requires a failed terminal Attempt")

        existing = next(
            (item for item in self._attempts.list_for_session(run_dir, session_id=session_id) if item.idempotency_key == value.idempotency_key),
            None,
        )
        if existing is not None:
            self._require_matching_replay(run_dir, attempt_id=existing.attempt_id, value=value)
            return BaselineRepairResult(
                status="reused",
                attempt=existing.model_dump(mode="json"),
                pipeline_job=_pipeline_job(run_dir, existing.pipeline_job_id),
            )

        if session.status != "FAILED" or session.baseline_status != "failed":
            raise ValueError("baseline repair requires a failed Session")
        completed = next(
            (item for item in self._attempts.list_for_session(run_dir, session_id=session_id)
             if item.job_type == "experiment_baseline" and item.runtime_status == "COMPLETED"),
            None,
        )
        if completed is not None:
            raise ValueError("baseline repair is not allowed after a completed baseline Attempt")
        if not session.evaluation_contract_ref or not session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: baseline evaluation contract is missing")
        if (failed.evaluation_contract_ref, failed.evaluation_contract_sha256) != (
            session.evaluation_contract_ref,
            session.evaluation_contract_sha256,
        ):
            raise ValueError("baseline repair target uses a different evaluation contract")

        binding = BaselineControlService._load_binding(run_dir, session)
        snapshot, python_executable = BaselineControlService._load_observed_environment(run_dir, session)
        contract_path = run_dir / session.evaluation_contract_ref
        if not contract_path.is_file() or sha256_file(contract_path) != session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: frozen evaluation contract changed")
        contract = EvaluationContract.model_validate_json(contract_path.read_text(encoding="utf-8"))
        inputs_path = run_dir / "experiments" / "execution_inputs" / f"{session_id}.json"
        if not inputs_path.is_file():
            raise ValueError("execution_contract_incomplete: baseline execution inputs are missing")
        inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
        dataset_sha = _required_sha(inputs, "dataset_manifest_sha256")
        asset_sha = _required_sha(inputs, "asset_manifest_sha256")
        repository = run_dir / binding.repository_ref
        adapter_result = ExecutorAdapter().inspect(repository)
        if adapter_result.status != "supported" or adapter_result.evidence is None:
            raise ValueError(adapter_result.blocker or "execution adapter is unsupported")
        if adapter_result.adapter_id != binding.adapter_id:
            raise ValueError("execution adapter differs from the frozen repository binding")
        self._validate_repair(value, adapter_result.evidence.allowed_paths, adapter_result.evidence.protected_paths, contract)
        protected_ref = failed.protected_artifact_report_ref
        protected_sha = failed.protected_artifact_report_sha256
        if not protected_ref or not protected_sha or not (run_dir / protected_ref).is_file():
            raise ValueError("execution_contract_incomplete: baseline protected artifact report is missing")

        request = ExecutorHandoffRequest(
            session_id=session_id,
            job_type="experiment_baseline",
            attempt_purpose="repair",
            idempotency_key=value.idempotency_key,
            repository_path=repository,
            base_commit="HEAD",
            environment_snapshot_ref=session.environment_snapshot_ref or "",
            adapter_inputs=ExecutorAdapterInputs(
                run_id=run_dir.name,
                worktree_ref="server-owned-by-handoff",
                repository_fingerprint=binding.repository_fingerprint,
                environment_sha256=snapshot.environment_sha256,
                dataset_manifest_sha256=dataset_sha,
                asset_manifest_sha256=asset_sha,
                python_executable=python_executable,
                timeout_seconds=contract.resource_budget.max_wall_seconds,
                evaluation_phase="b_dev",
                split_ref=str(resolve_split_artifact(run_dir, contract.b_dev_ref)),
            ),
            intervention_contract=value.intervention_contract,
            job_timeout_sec=contract.resource_budget.max_wall_seconds,
            additional_protected_paths=[metric.implementation_ref for metric in contract.metrics],
            required_device_count=contract.required_device_count,
            required_vram_mb=contract.required_vram_mb,
            evaluation_contract_ref=session.evaluation_contract_ref,
            evaluation_contract_sha256=session.evaluation_contract_sha256,
            protected_artifact_report_ref=protected_ref,
            protected_artifact_report_sha256=protected_sha,
        )
        result = ExecutorAttemptHandoffService().handoff(
            run_dir,
            request=request,
            proposal_provider=lambda _tools: value.approved_proposal,
        )
        if result.status != "queued" or result.attempt is None or result.pipeline_job is None:
            return BaselineRepairResult(
                status=result.status,
                workspace_ref=_workspace_ref(run_dir, result.workspace),
                blocker=result.blocker,
            )
        self._write_request_receipt(run_dir, attempt_id=str(result.attempt["attempt_id"]), value=value)
        return BaselineRepairResult(
            status="queued",
            attempt=result.attempt,
            pipeline_job=result.pipeline_job,
            workspace_ref=_workspace_ref(run_dir, result.workspace),
        )

    @staticmethod
    def _validate_repair(value: BaselineRepairInput, adapter_allowed: list[str], adapter_protected: list[str], contract: EvaluationContract) -> None:
        allowed = set(adapter_allowed)
        protected = set(adapter_protected) | {metric.implementation_ref for metric in contract.metrics}
        if not value.intervention_contract.allowed_paths or not set(value.intervention_contract.allowed_paths) <= allowed:
            raise ValueError("baseline repair path is outside the adapter allowlist")
        if not set(value.intervention_contract.target_modules) <= set(value.intervention_contract.allowed_paths):
            raise ValueError("baseline repair target modules must be editable paths")
        if set(value.intervention_contract.allowed_paths) & protected:
            raise ValueError("baseline repair includes a protected path")
        if not value.approved_proposal.edits:
            raise ValueError("baseline repair proposal must contain at least one edit")
        for edit in value.approved_proposal.edits:
            if edit.path not in value.intervention_contract.allowed_paths or edit.path in protected:
                raise ValueError("baseline repair patch is outside the reviewed path boundary")
            if not edit.search or edit.search == edit.replace:
                raise ValueError("baseline repair proposal contains an empty edit")
        if value.intervention_contract.time_budget > contract.resource_budget.max_wall_seconds:
            raise ValueError("baseline repair exceeds the frozen time budget")

    @staticmethod
    def _receipt_path(run_dir: Path, attempt_id: str) -> Path:
        return run_dir / "attempts" / attempt_id / "repair_request.json"

    @classmethod
    def _write_request_receipt(cls, run_dir: Path, *, attempt_id: str, value: BaselineRepairInput) -> None:
        path = cls._receipt_path(run_dir, attempt_id)
        payload = {"schema_version": 1, "repair_request_sha256": canonical_sha256(value)}
        if path.is_file():
            if json.loads(path.read_text(encoding="utf-8")) != payload:
                raise ValueError("idempotency_conflict: repair request receipt differs")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def _require_matching_replay(cls, run_dir: Path, *, attempt_id: str, value: BaselineRepairInput) -> None:
        path = cls._receipt_path(run_dir, attempt_id)
        if not path.is_file():
            raise ValueError("idempotency_conflict: existing repair request receipt is missing")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("repair_request_sha256") != canonical_sha256(value):
            raise ValueError("idempotency_conflict: repair request differs from the existing Attempt")


def _required_sha(payload: object, key: str) -> str:
    value = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"execution_contract_incomplete: execution input {key} is invalid")
    return value


def _pipeline_job(run_dir: Path, job_id: str | None) -> dict:
    from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs

    job = next((item for item in load_pipeline_jobs(run_dir) if item.get("job_id") == job_id), None)
    if job is None:
        raise FileNotFoundError("repair PipelineJob is missing")
    return job


def _workspace_ref(run_dir: Path, workspace) -> str | None:
    if workspace is None:
        return None
    return str(Path(workspace.worktree_path).resolve().relative_to(run_dir.resolve()))
