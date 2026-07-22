"""Bridge an Executor worktree to the existing Attempt/Job control plane."""
from __future__ import annotations
import json, shutil
from hashlib import sha256
from pathlib import Path
from typing import Callable, Literal
from pydantic import BaseModel, ConfigDict, Field
from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.experiment.attempt_service import ExperimentAttemptService, ExperimentAttemptStartResult
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter, ExecutorAdapterInputs
from autoad_researcher.experiment.executor_agent import ExecutorAgent, ExecutorLimits, ExecutorProposal, ExecutorSummary
from autoad_researcher.experiment.executor_contracts import InterventionContract, WorkspaceSpec
from autoad_researcher.experiment.intervention_admission import InterventionAdmissionService
from autoad_researcher.experiment.worktree import WorktreeManager

class ExecutorHandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    job_type: Literal["experiment_baseline", "experiment_baseline_b_test", "experiment_attempt", "experiment_confirmatory"]
    idempotency_key: str
    repository_path: Path
    base_commit: str
    environment_snapshot_ref: str
    adapter_inputs: ExecutorAdapterInputs
    intervention_contract: InterventionContract
    job_timeout_sec: int = Field(gt=0)
    evaluation_contract_ref: str
    evaluation_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_artifact_report_ref: str
    protected_artifact_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

class ExecutorHandoffResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["queued", "blocked"]
    blocker: str | None = None
    attempt: dict | None = None
    pipeline_job: dict | None = None
    workspace: WorkspaceSpec | None = None

class ExecutorAttemptHandoffService:
    def __init__(self, *, attempt_service: ExperimentAttemptService | None = None, adapter: ExecutorAdapter | None = None, admission_service: InterventionAdmissionService | None = None):
        self._attempts = attempt_service or ExperimentAttemptService(); self._adapter = adapter or ExecutorAdapter(); self._admission = admission_service or InterventionAdmissionService()
    def handoff(self, run_dir: Path, *, request: ExecutorHandoffRequest, proposal_provider: Callable) -> ExecutorHandoffResult:
        adapter_result = self._adapter.inspect(request.repository_path)
        if adapter_result.status != "supported" or adapter_result.evidence is None:
            return ExecutorHandoffResult(status="blocked", blocker=adapter_result.blocker)
        key = sha256(request.idempotency_key.encode()).hexdigest()[:16]
        workspace_ref = f"executor_worktrees/{key}"
        manager = WorktreeManager(run_dir / "executor_worktrees")
        workspace = manager.create(repository_path=request.repository_path, attempt_id=key, base_commit=request.base_commit, protected_paths=adapter_result.evidence.protected_paths, environment_snapshot_ref=request.environment_snapshot_ref)
        staging = run_dir / "executor_staging" / key
        summary_path = staging / "executor_summary.json"
        admission_path = staging / "intervention_admission.json"
        summary = (
            ExecutorSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
            if summary_path.is_file() and admission_path.is_file()
            else ExecutorAgent(contract=request.intervention_contract, workspace=workspace, artifact_dir=staging, limits=ExecutorLimits(max_wall_seconds=request.intervention_contract.time_budget)).run(proposal_provider)
        )
        if summary.status != "completed": return ExecutorHandoffResult(status="blocked", blocker=summary.error, workspace=workspace)
        plan, refs = self._adapter.build_execution(adapter_result, request.adapter_inputs.model_copy(update={"worktree_ref": workspace_ref}))
        admission = self._admission.admit(contract=request.intervention_contract, workspace=workspace, summary=summary, artifact_dir=staging, command_plan=plan)
        if not admission.allowed:
            return ExecutorHandoffResult(status="blocked", blocker=f"{admission.code}: {admission.detail}", workspace=workspace)
        started = self._attempts.create_or_get_attempt(run_dir, session_id=request.session_id, job_type=request.job_type, idempotency_key=request.idempotency_key, command_plan=plan, input_refs=refs, job_timeout_sec=request.job_timeout_sec, evaluation_contract_ref=request.evaluation_contract_ref, evaluation_contract_sha256=request.evaluation_contract_sha256, protected_artifact_report_ref=request.protected_artifact_report_ref, protected_artifact_report_sha256=request.protected_artifact_report_sha256)
        artifact_dir = run_dir / "attempts" / started.attempt.attempt_id; artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "intervention_contract.json").write_text(request.intervention_contract.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (artifact_dir / "workspace.json").write_text(workspace.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (artifact_dir / "changed_files.json").write_text(json.dumps(summary.changed_files, indent=2) + "\n", encoding="utf-8")
        for name in ["patch.diff", "final_patch.diff", "intervention_admission.json", "repair_log.jsonl", "executor_summary.json"]:
            source = staging / name
            if source.is_file(): shutil.copy2(source, artifact_dir / name)
        return ExecutorHandoffResult(status="queued", attempt=started.attempt.model_dump(mode="json"), pipeline_job=started.pipeline_job, workspace=workspace)
