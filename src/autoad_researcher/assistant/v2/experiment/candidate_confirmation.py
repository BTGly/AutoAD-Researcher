"""Held-out confirmation for one admitted candidate Attempt.

The service deliberately receives no command, repository path, or metrics.  It
turns a completed B_dev candidate into a B_test Attempt only from durable
Session/Attempt artifacts, then records an immutable candidate snapshot when
the Worker has finalized that confirmation.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.experiment.baseline_control import BaselineControlService, resolve_split_artifact
from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.experiment.attempt_service import ExperimentAttemptService, ExperimentAttemptStartResult
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter, ExecutorAdapterInputs
from autoad_researcher.experiment.executor_contracts import WorkspaceSpec
from autoad_researcher.experiment.intervention_admission import InterventionAdmission
from autoad_researcher.experiment.promotion import CandidateRegistry, CandidateSnapshot, DecisionEngine
from autoad_researcher.experiment.scientific_assessment import (
    ScientificAssessmentInputsStore,
    ScientificAssessmentService,
    ScientificEvaluationInputs,
)
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.experiment.validity import ComparisonIdentity


class CandidateConfirmationInput(BaseModel):
    """The explicit scientific decision needed to spend a held-out evaluation."""

    model_config = ConfigDict(extra="forbid")

    candidate_attempt_id: str = Field(pattern=r"^attempt_[0-9]{6}$")
    noise_threshold: float = Field(ge=0)
    idempotency_key: str = Field(min_length=1)


class CandidateConfirmationLink(BaseModel):
    """Immutable lineage linking a B_test Attempt to its B_dev implementation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    candidate_attempt_id: str = Field(pattern=r"^attempt_[0-9]{6}$")
    noise_threshold: float = Field(ge=0)
    source_branch: str = Field(min_length=1)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class CandidateConfirmationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started: ExperimentAttemptStartResult
    candidate_snapshot_ref: str | None = None


class CandidateConfirmationService:
    """Run explicit B_test only for a valid B_dev candidate."""

    def __init__(self) -> None:
        self._sessions = ExperimentSessionStore()
        self._attempt_store = ExperimentAttemptStore()
        self._attempts = ExperimentAttemptService()
        self._assessments = ScientificAssessmentService()
        self._assessment_inputs = ScientificAssessmentInputsStore()
        self._registry = CandidateRegistry()

    def start(
        self,
        run_dir: Path,
        *,
        session_id: str,
        value: CandidateConfirmationInput,
    ) -> CandidateConfirmationResult:
        session = self._sessions.load(run_dir, session_id)
        if session is None:
            raise FileNotFoundError("experiment session not found")
        if session.status != "READY" or session.baseline_status != "completed":
            raise ValueError("candidate confirmation requires a completed baseline Session")
        candidate = self._required_attempt(run_dir, value.candidate_attempt_id, session_id, "experiment_attempt")
        if candidate.runtime_status != "COMPLETED":
            raise ValueError("candidate confirmation requires a completed candidate Attempt")
        existing = next(
            (
                item
                for item in self._attempt_store.list_for_session(run_dir, session_id=session_id)
                if item.idempotency_key == value.idempotency_key
            ),
            None,
        )
        if existing is not None:
            link = self._load_link(run_dir, existing.attempt_id)
            if (
                existing.job_type != "experiment_confirmatory"
                or link is None
                or link.candidate_attempt_id != value.candidate_attempt_id
                or link.noise_threshold != value.noise_threshold
            ):
                raise ValueError("idempotency_conflict: confirmation request differs from the existing Attempt")
            return CandidateConfirmationResult(
                started=ExperimentAttemptStartResult(
                    attempt=existing,
                    pipeline_job=self._pipeline_job(run_dir, existing.pipeline_job_id),
                    disposition="reused",
                ),
                candidate_snapshot_ref=self.finalize_if_ready(run_dir, confirmation_attempt_id=existing.attempt_id),
            )
        b_dev = self._assessments.effective_assessment(run_dir, attempt_id=candidate.attempt_id)
        decision = DecisionEngine().decide(assessment=b_dev, phase="b_dev", noise_threshold=value.noise_threshold)
        if decision.action != "candidate":
            raise ValueError(f"candidate confirmation is not eligible: {decision.action}")
        b_test_baseline = self._baseline_b_test(run_dir, session_id)
        binding = BaselineControlService._load_binding(run_dir, session)
        snapshot, python_executable = BaselineControlService._load_observed_environment(run_dir, session)
        contract = self._contract(run_dir, session)
        workspace = self._workspace(run_dir, candidate.attempt_id)
        source_commit = self._commit_admitted_candidate(run_dir, candidate.attempt_id, workspace)
        adapter = ExecutorAdapter()
        adapter_result = adapter.inspect(Path(workspace.worktree_path))
        if adapter_result.status != "supported" or adapter_result.evidence is None:
            raise ValueError(adapter_result.blocker or "execution adapter is unsupported")
        inputs_payload = json.loads((run_dir / "experiments" / "execution_inputs" / f"{session_id}.json").read_text(encoding="utf-8"))
        adapter_inputs = ExecutorAdapterInputs(
            run_id=run_dir.name,
            worktree_ref=str(Path(workspace.worktree_path).resolve().relative_to(run_dir.resolve())),
            repository_fingerprint=binding.repository_fingerprint,
            environment_sha256=snapshot.environment_sha256,
            dataset_manifest_sha256=str(inputs_payload["dataset_manifest_sha256"]),
            asset_manifest_sha256=str(inputs_payload["asset_manifest_sha256"]),
            python_executable=python_executable,
            timeout_seconds=contract.resource_budget.max_wall_seconds,
            evaluation_phase="b_test",
            split_ref=str(resolve_split_artifact(run_dir, contract.b_test_ref)),
        )
        plan, refs = adapter.build_execution(adapter_result, adapter_inputs)
        protected_ref = f"experiments/protected_artifacts/{session_id}.json"
        protected_path = run_dir / protected_ref
        if not protected_path.is_file():
            raise ValueError("execution_contract_incomplete: baseline protected artifact report is missing")
        started = self._attempts.create_or_get_attempt(
            run_dir,
            session_id=session_id,
            job_type="experiment_confirmatory",
            idempotency_key=value.idempotency_key,
            command_plan=plan,
            input_refs=refs,
            job_timeout_sec=contract.resource_budget.max_wall_seconds,
            required_device_count=contract.required_device_count,
            required_vram_mb=contract.required_vram_mb,
            evaluation_contract_ref=session.evaluation_contract_ref,
            evaluation_contract_sha256=session.evaluation_contract_sha256,
            protected_artifact_report_ref=protected_ref,
            protected_artifact_report_sha256=sha256_file(protected_path),
        )
        confirmation_id = started.attempt.attempt_id
        identity = ComparisonIdentity(
            dataset_identity=contract.dataset_identity,
            split_identity=contract.split_identity,
            seed=self._candidate_seed(run_dir, candidate.attempt_id),
            checkpoint_selection=contract.checkpoint_selection,
            command_sha256=refs.command_sha256,
            metric_implementation_refs=[metric.implementation_ref for metric in contract.metrics],
            evaluation_contract_sha256=session.evaluation_contract_sha256 or "",
            outputs_complete=True,
        )
        self._assessment_inputs.save(
            run_dir / "attempts" / confirmation_id,
            ScientificEvaluationInputs(
                baseline_metrics=self._metrics(run_dir, b_test_baseline.attempt_id),
                candidate_identity=identity,
                baseline_identity=identity,
            ),
        )
        self._write_link(
            run_dir,
            confirmation_id,
            CandidateConfirmationLink(
                candidate_attempt_id=candidate.attempt_id,
                noise_threshold=value.noise_threshold,
                source_branch=workspace.branch,
                source_commit=source_commit,
            ),
        )
        append_event(run_dir, "experiment.candidate.b_test_queued", {"candidate_attempt_id": candidate.attempt_id, "confirmation_attempt_id": confirmation_id})
        return CandidateConfirmationResult(started=started, candidate_snapshot_ref=self.finalize_if_ready(run_dir, confirmation_attempt_id=confirmation_id))

    def finalize_if_ready(self, run_dir: Path, *, confirmation_attempt_id: str) -> str | None:
        confirmation = self._attempt_store.load(run_dir, confirmation_attempt_id)
        if confirmation is None:
            raise FileNotFoundError("confirmation Attempt not found")
        if confirmation.job_type != "experiment_confirmatory" or confirmation.runtime_status != "COMPLETED":
            return None
        link = self._load_link(run_dir, confirmation_attempt_id)
        if link is None:
            return None
        candidate = self._required_attempt(run_dir, link.candidate_attempt_id, confirmation.session_id, "experiment_attempt")
        session = self._sessions.load(run_dir, confirmation.session_id)
        if session is None or not session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: confirmation Session contract is missing")
        b_test = self._assessments.effective_assessment(run_dir, attempt_id=confirmation_attempt_id)
        decision = DecisionEngine().decide(assessment=b_test, phase="b_test", noise_threshold=link.noise_threshold)
        if decision.action != "ready_for_promotion":
            return None
        candidate_id = f"candidate_{int(candidate.attempt_id.rsplit('_', 1)[1]):06d}"
        existing_path = run_dir / "experiments" / "champions" / "candidates" / f"{candidate_id}.json"
        if existing_path.is_file():
            existing = self._registry.load_candidate(run_dir, candidate_id)
            if existing.attempt_id != candidate.attempt_id or existing.session_id != confirmation.session_id:
                raise ValueError("candidate ID already exists for different evidence")
            return str(existing_path.relative_to(run_dir))
        admission = InterventionAdmission.model_validate_json((run_dir / "attempts" / candidate.attempt_id / "intervention_admission.json").read_text(encoding="utf-8"))
        if not admission.allowed or admission.patch_sha256 is None:
            raise ValueError("candidate admission evidence is missing")
        snapshot = CandidateSnapshot(
            candidate_id=candidate_id,
            session_id=confirmation.session_id,
            evaluation_contract_hash=session.evaluation_contract_sha256,
            idea_id=json.loads((run_dir / "attempts" / candidate.attempt_id / "intervention_contract.json").read_text(encoding="utf-8"))["idea_id"],
            attempt_id=candidate.attempt_id,
            source_branch=link.source_branch,
            source_commit=link.source_commit,
            patch_sha256=admission.patch_sha256,
            metrics_ref=f"attempts/{candidate.attempt_id}/metrics.json",
            resource_ref=f"attempts/{candidate.attempt_id}/execution_result.json",
            b_dev_evidence_ref=f"attempts/{candidate.attempt_id}/scientific_assessment.json",
            b_test_evidence_ref=f"attempts/{confirmation_attempt_id}/scientific_assessment.json",
            b_test_passed=True,
            guardrails_passed=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        ref = self._registry.create_candidate(run_dir, snapshot)
        append_event(run_dir, "experiment.candidate.registered", {"candidate_id": candidate_id, "attempt_id": candidate.attempt_id, "confirmation_attempt_id": confirmation_attempt_id})
        return ref

    def _baseline_b_test(self, run_dir: Path, session_id: str):
        return next((item for item in self._attempt_store.list_for_session(run_dir, session_id=session_id) if item.job_type == "experiment_baseline_b_test" and item.runtime_status == "COMPLETED"), None) or self._raise("candidate confirmation requires a completed B_test baseline")

    @staticmethod
    def _required_attempt(run_dir: Path, attempt_id: str, session_id: str, job_type: str):
        attempt = ExperimentAttemptStore().load(run_dir, attempt_id)
        if attempt is None or attempt.session_id != session_id or attempt.job_type != job_type:
            raise ValueError("Attempt does not belong to this confirmation")
        return attempt

    @staticmethod
    def _contract(run_dir: Path, session):
        from autoad_researcher.experiment.evaluation_contract import EvaluationContract
        if not session.evaluation_contract_ref or not session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: frozen evaluation contract is missing")
        path = run_dir / session.evaluation_contract_ref
        if not path.is_file() or sha256_file(path) != session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: frozen evaluation contract changed")
        return EvaluationContract.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _workspace(run_dir: Path, attempt_id: str) -> WorkspaceSpec:
        path = run_dir / "attempts" / attempt_id / "workspace.json"
        workspace = WorkspaceSpec.model_validate_json(path.read_text(encoding="utf-8"))
        root = Path(workspace.worktree_path).resolve()
        allowed = (run_dir / "executor_worktrees").resolve()
        if not root.is_relative_to(allowed):
            raise ValueError("candidate workspace is outside the run-owned executor area")
        return workspace

    @staticmethod
    def _commit_admitted_candidate(run_dir: Path, attempt_id: str, workspace: WorkspaceSpec) -> str:
        attempt_dir = run_dir / "attempts" / attempt_id
        admission = InterventionAdmission.model_validate_json((attempt_dir / "intervention_admission.json").read_text(encoding="utf-8"))
        if not admission.allowed or not admission.changed_files or admission.patch_sha256 is None:
            raise ValueError("candidate admission evidence is missing")
        root = Path(workspace.worktree_path)
        final_patch = attempt_dir / "final_patch.diff"
        if not final_patch.is_file() or sha256_file(final_patch) != admission.patch_sha256:
            raise ValueError("candidate admission patch evidence is missing")
        diff = subprocess.run(["git", "diff", "--", *admission.changed_files], cwd=root, check=True, capture_output=True, text=True).stdout
        # Admission writes Git's textual diff through a helper that strips its
        # terminal newline; preserve all substantive bytes while accepting that
        # serialization-only newline difference.
        if diff.rstrip("\n") != final_patch.read_text(encoding="utf-8").rstrip("\n"):
            raise ValueError("candidate worktree differs from admitted patch evidence")
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True).stdout.splitlines()
        changed = {line[3:] for line in status if len(line) >= 4}
        if changed != set(admission.changed_files):
            raise ValueError("candidate worktree contains changes outside admitted evidence")
        subprocess.run(["git", "add", "--", *admission.changed_files], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=AutoAD", "-c", "user.email=autoad@invalid", "commit", "--no-gpg-sign", "-m", f"AutoAD candidate {attempt_id}"], cwd=root, check=True, capture_output=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    @staticmethod
    def _candidate_seed(run_dir: Path, attempt_id: str) -> int:
        inputs = ScientificAssessmentInputsStore().load(run_dir / "attempts" / attempt_id)
        return inputs.candidate_identity.seed

    @staticmethod
    def _metrics(run_dir: Path, attempt_id: str) -> dict[str, float]:
        raw = json.loads((run_dir / "attempts" / attempt_id / "outcome_card.json").read_text(encoding="utf-8")).get("metrics")
        if not isinstance(raw, dict) or not all(isinstance(value, (int, float)) for value in raw.values()):
            raise ValueError("execution_contract_incomplete: baseline metrics are unavailable")
        return {str(key): float(value) for key, value in raw.items()}

    @staticmethod
    def _link_path(run_dir: Path, attempt_id: str) -> Path:
        return run_dir / "attempts" / attempt_id / "candidate_confirmation.json"

    def _write_link(self, run_dir: Path, attempt_id: str, value: CandidateConfirmationLink) -> None:
        path = self._link_path(run_dir, attempt_id)
        if path.is_file():
            if CandidateConfirmationLink.model_validate_json(path.read_text(encoding="utf-8")) != value:
                raise ValueError("idempotency_conflict: confirmation request differs from the existing Attempt")
            return
        path.write_text(value.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _load_link(self, run_dir: Path, attempt_id: str) -> CandidateConfirmationLink | None:
        path = self._link_path(run_dir, attempt_id)
        return CandidateConfirmationLink.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    @staticmethod
    def _pipeline_job(run_dir: Path, job_id: str | None) -> dict[str, object]:
        from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs

        job = next((item for item in load_pipeline_jobs(run_dir) if item.get("job_id") == job_id), None)
        if job is None:
            raise FileNotFoundError("confirmation PipelineJob is missing")
        return job

    @staticmethod
    def _raise(message: str):
        raise ValueError(message)
