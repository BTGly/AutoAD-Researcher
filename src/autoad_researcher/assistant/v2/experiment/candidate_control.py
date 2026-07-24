"""Controlled candidate launch from a baseline-complete Session.

The HTTP caller supplies an explicitly approved intervention and structured
patch proposal.  Repository location, environment, command, input hashes, and
the baseline comparison are recovered only from immutable Session artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.experiment.baseline_control import BaselineControlService, resolve_split_artifact
from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter, ExecutorAdapterInputs
from autoad_researcher.experiment.executor_agent import ExecutorProposal
from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.experiment.executor_handoff import ExecutorHandoffRequest
from autoad_researcher.experiment.idea_tree import IdeaTreeStore
from autoad_researcher.experiment.scientific_assessment import (
    ScientificEvaluationInputs,
    ScientificExecutorHandoffService,
    load_declared_metric_values,
)
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.experiment.validity import ComparisonIdentity


class CandidateLaunchInput(BaseModel):
    """A concrete, user-approved candidate action; never an arbitrary command."""

    model_config = ConfigDict(extra="forbid")

    intervention_contract: InterventionContract
    approved_proposal: ExecutorProposal
    comparison_seed: int
    idempotency_key: str = Field(min_length=1)


class CandidateLaunchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    attempt: dict | None = None
    pipeline_job: dict | None = None
    workspace_ref: str | None = None
    blocker: str | None = None


class CandidateControlService:
    """Bridge a reviewed candidate diff to the existing scientific handoff."""

    def __init__(self) -> None:
        self._sessions = ExperimentSessionStore()
        self._attempts = ExperimentAttemptStore()
        self._trees = IdeaTreeStore()

    def start(self, run_dir: Path, *, session_id: str, value: CandidateLaunchInput) -> CandidateLaunchResult:
        session = self._sessions.load(run_dir, session_id)
        if session is None:
            raise FileNotFoundError("experiment session not found")
        if not (
            (session.status == "READY_FOR_BASELINE" and session.baseline_status == "b_dev_completed")
            or (session.status == "READY" and session.baseline_status == "completed")
        ):
            raise ValueError("candidate launch requires a completed baseline B_dev")
        if session.authorization.execution_mode == "plan_only":
            raise ValueError("plan_only Session may not launch a candidate")
        if not session.evaluation_contract_ref or not session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: baseline evaluation contract is missing")

        existing = next((item for item in self._attempts.list_for_session(run_dir, session_id=session_id)
                         if item.idempotency_key == value.idempotency_key), None)
        if existing is not None:
            self._require_matching_replay(run_dir, attempt_id=existing.attempt_id, value=value)
            return CandidateLaunchResult(status="reused", attempt=existing.model_dump(mode="json"), pipeline_job=_pipeline_job(run_dir, existing.pipeline_job_id))

        binding = BaselineControlService._load_binding(run_dir, session)
        snapshot, python_executable = BaselineControlService._load_observed_environment(run_dir, session)
        contract_path = run_dir / session.evaluation_contract_ref
        if not contract_path.is_file() or sha256_file(contract_path) != session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: frozen evaluation contract changed")
        from autoad_researcher.experiment.evaluation_contract import EvaluationContract
        contract = EvaluationContract.model_validate_json(contract_path.read_text(encoding="utf-8"))
        if value.comparison_seed not in contract.seeds:
            raise ValueError("candidate comparison seed is not in the frozen evaluation contract")
        tree = self._trees.load(run_dir, session_id=session_id)
        if tree is None:
            raise ValueError("execution_contract_incomplete: IdeaTree is missing")
        node = tree.node(value.intervention_contract.idea_id)
        if node.is_root or node.status in {"PRUNED", "MERGED"}:
            raise ValueError("candidate idea is not eligible for execution")
        if node.mechanism != value.intervention_contract.mechanism or node.hypothesis != value.intervention_contract.hypothesis:
            raise ValueError("candidate intervention must match its IdeaTree node")

        baseline = next((item for item in self._attempts.list_for_session(run_dir, session_id=session_id)
                         if item.job_type == "experiment_baseline" and item.runtime_status == "COMPLETED"), None)
        if baseline is None:
            raise ValueError("candidate launch requires a completed baseline Attempt")
        baseline_metrics = _metrics(run_dir, baseline.attempt_id)
        inputs_payload = json.loads((run_dir / "experiments" / "execution_inputs" / f"{session_id}.json").read_text(encoding="utf-8"))
        dataset_sha = str(inputs_payload["dataset_manifest_sha256"])
        asset_sha = str(inputs_payload["asset_manifest_sha256"])
        adapter_inputs = ExecutorAdapterInputs(
            run_id=run_dir.name,
            worktree_ref="server-owned-by-handoff",
            repository_fingerprint=binding.repository_fingerprint,
            environment_sha256=snapshot.environment_sha256,
            dataset_manifest_sha256=dataset_sha,
            asset_manifest_sha256=asset_sha,
            python_executable=python_executable,
            timeout_seconds=contract.resource_budget.max_wall_seconds,
            split_ref=str(resolve_split_artifact(run_dir, contract.b_dev_ref)),
        )
        command_identity = _semantic_command_identity(ExecutorAdapter(), run_dir / binding.repository_ref, adapter_inputs)
        identity = ComparisonIdentity(
            dataset_identity=contract.dataset_identity,
            split_identity=contract.split_identity,
            seed=value.comparison_seed,
            checkpoint_selection=contract.checkpoint_selection,
            command_sha256=command_identity,
            metric_implementation_refs=[metric.implementation_ref for metric in contract.metrics],
            evaluation_contract_sha256=session.evaluation_contract_sha256,
            outputs_complete=True,
        )
        protected_ref = f"experiments/protected_artifacts/{session_id}.json"
        protected_path = run_dir / protected_ref
        if not protected_path.is_file():
            raise ValueError("execution_contract_incomplete: baseline protected artifact report is missing")
        request = ExecutorHandoffRequest(
            session_id=session_id,
            job_type="experiment_attempt",
            idempotency_key=value.idempotency_key,
            repository_path=run_dir / binding.repository_ref,
            base_commit="HEAD",
            environment_snapshot_ref=session.environment_snapshot_ref or "",
            adapter_inputs=adapter_inputs,
            intervention_contract=value.intervention_contract,
            job_timeout_sec=contract.resource_budget.max_wall_seconds,
            additional_protected_paths=[metric.implementation_ref for metric in contract.metrics],
            required_device_count=contract.required_device_count,
            required_vram_mb=contract.required_vram_mb,
            evaluation_contract_ref=session.evaluation_contract_ref,
            evaluation_contract_sha256=session.evaluation_contract_sha256,
            protected_artifact_report_ref=protected_ref,
            protected_artifact_report_sha256=sha256_file(protected_path),
        )
        result = ScientificExecutorHandoffService().handoff(
            run_dir,
            request=request,
            scientific_inputs=ScientificEvaluationInputs(
                baseline_metrics=baseline_metrics,
                candidate_identity=identity,
                baseline_identity=identity,
            ),
            proposal_provider=lambda _tools: value.approved_proposal,
        )
        if result.status == "queued" and result.attempt is not None:
            attempt_id = str(result.attempt["attempt_id"])
            self._write_request_receipt(run_dir, attempt_id=attempt_id, value=value)
            latest = self._trees.load(run_dir, session_id=session_id)
            assert latest is not None
            self._trees.attach_attempt(run_dir, session_id=session_id, expected_revision=latest.revision,
                                       idempotency_key=f"candidate-attempt:{attempt_id}", node_id=node.node_id,
                                       attempt_ref=f"attempts/{attempt_id}")
        return CandidateLaunchResult(status=result.status, attempt=result.attempt, pipeline_job=result.pipeline_job,
                                     workspace_ref=(str(Path(result.workspace.worktree_path).resolve().relative_to(run_dir.resolve())) if result.workspace else None), blocker=result.blocker)

    @staticmethod
    def _request_receipt_path(run_dir: Path, attempt_id: str) -> Path:
        return run_dir / "attempts" / attempt_id / "candidate_request.json"

    @classmethod
    def _write_request_receipt(cls, run_dir: Path, *, attempt_id: str, value: CandidateLaunchInput) -> None:
        path = cls._request_receipt_path(run_dir, attempt_id)
        payload = {"schema_version": 1, "candidate_request_sha256": canonical_sha256(value)}
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise ValueError("idempotency_conflict: candidate request receipt differs")
            return
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def _require_matching_replay(cls, run_dir: Path, *, attempt_id: str, value: CandidateLaunchInput) -> None:
        path = cls._request_receipt_path(run_dir, attempt_id)
        if not path.is_file():
            raise ValueError("idempotency_conflict: existing candidate request receipt is missing")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("idempotency_conflict: existing candidate request receipt is invalid") from exc
        if payload.get("candidate_request_sha256") != canonical_sha256(value):
            raise ValueError("idempotency_conflict: candidate request differs from the existing Attempt")


def _metrics(run_dir: Path, attempt_id: str) -> dict[str, float]:
    return load_declared_metric_values(run_dir, attempt_id=attempt_id)


def _semantic_command_identity(adapter: ExecutorAdapter, repository: Path, inputs: ExecutorAdapterInputs) -> str:
    inspected = adapter.inspect(repository)
    plan, _ = adapter.build_execution(inspected, inputs)
    return canonical_sha256({"program": plan.program, "args": plan.args, "expected_outputs": plan.expected_outputs, "network": plan.network})


def _pipeline_job(run_dir: Path, job_id: str | None) -> dict:
    from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
    job = next((item for item in load_pipeline_jobs(run_dir) if item.get("job_id") == job_id), None)
    if job is None:
        raise FileNotFoundError("candidate PipelineJob is missing")
    return job
