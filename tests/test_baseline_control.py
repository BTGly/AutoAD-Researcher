from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.experiment.baseline_control import BaselineContractInput, BaselineControlService
from autoad_researcher.assistant.v2.experiment.candidate_control import CandidateControlService, CandidateLaunchInput
from autoad_researcher.assistant.v2.experiment.candidate_confirmation import CandidateConfirmationInput, CandidateConfirmationService
from autoad_researcher.assistant.v2.experiment.promotion_control import PromotionControlService, PromotionInput
from autoad_researcher.assistant.v2.execution_repository import ExecutionRepositoryBinding
from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.environments.context_collector import CollectedValidationContext
from autoad_researcher.environments.snapshot import EnvironmentSnapshot
from autoad_researcher.environments.validation import ValidationContext
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.executor_agent import ExecutorProposal
from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.experiment.idea_tree import IdeaTreeStore
from autoad_researcher.experiment.patch_protocol import SearchReplaceEdit
from autoad_researcher.experiment.promotion import CandidateRegistry
from autoad_researcher.experiment.scientific_assessment import ScientificAssessmentService
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.worker.main import _process_pending_jobs


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


def _ready_session(run_dir: Path):
    repository = run_dir / "repos" / "source_micro"
    repository.mkdir(parents=True)
    (repository / "evaluate.py").write_text("protected = True\n", encoding="utf-8")
    (repository / "run.py").write_text(
        "import json, os\nfrom pathlib import Path\n"
        "Path(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({'score': 0.8}))\n",
        encoding="utf-8",
    )
    manifest = {
        "adapter_id": "generic_python", "entrypoint": "run.py", "smoke_argv": [sys.executable, "run.py"],
        "metrics_output": "metrics.json", "allowed_paths": ["run.py"], "protected_paths": ["evaluate.py"],
        "activation_evidence": "observed",
    }
    (repository / "autoad_executor_adapter.json").write_text(json.dumps(manifest), encoding="utf-8")
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "fixture")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "baseline")

    (run_dir / "inputs").mkdir()
    (run_dir / "inputs" / "dev.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "inputs" / "test.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "input_task.yaml").write_text(
        "run_id: run\nrequest: baseline\nsource_ids: []\nprimary_metrics: [score]\nconstraints: []\n",
        encoding="utf-8",
    )
    binding = ExecutionRepositoryBinding(
        source_id="source_micro", source_kind="local_repo", repository_ref="repos/source_micro",
        repository_fingerprint="a" * 64, attestation_ref="repo_acquisition/source_micro/repository_attestation.json",
        attestation_sha256="b" * 64, adapter_manifest_ref="repos/source_micro/autoad_executor_adapter.json",
        adapter_manifest_sha256=sha256_file(repository / "autoad_executor_adapter.json"), adapter_id="generic_python", adapter_evidence={},
    )
    binding_path = run_dir / "task_bridge" / "execution_repository_binding.json"
    binding_path.parent.mkdir()
    binding_path.write_text(binding.model_dump_json(), encoding="utf-8")
    environment = run_dir / "environment"
    environment.mkdir()
    snapshot = EnvironmentSnapshot(
        schema_version=1, environment_kind="existing_python", platform="linux", repository_fingerprint="a" * 64,
        environment_sha256="c" * 64,
    )
    (environment / "snapshot.json").write_text(snapshot.model_dump_json(), encoding="utf-8")
    context = CollectedValidationContext(
        python_executable=sys.executable, context=ValidationContext(runtime_versions={}),
        package_inventory_sha256="d" * 64, command_results=[], repository_fingerprint="a" * 64,
    )
    (environment / "validation_context_r0.json").write_text(context.model_dump_json(), encoding="utf-8")
    sessions = ExperimentSessionStore()
    session, _ = sessions.create_or_get(
        run_dir, task_ref="input_task.yaml", task_hash="e" * 64, execution_mode="agent_assisted_after_approval",
        repository_ref=binding.repository_ref, execution_repository_binding_ref="task_bridge/execution_repository_binding.json",
        execution_repository_binding_sha256=canonical_sha256(binding),
    )
    return sessions.update_environment_state(
        run_dir, session_id=session.session_id, status="READY_FOR_BASELINE", environment_status="ready",
        readiness_status="ready", readiness_blockers=[], environment_snapshot_ref="environment/snapshot.json",
    )


def _contract() -> BaselineContractInput:
    return BaselineContractInput(
        primary_metric="score", primary_metric_direction="maximize", dataset_identity="fixture-dataset",
        split_identity="fixture-split", b_dev_ref="inputs/dev.json", b_test_ref="inputs/test.json",
        category_set=["fixture"], seeds=[1], checkpoint_selection="not_applicable", max_wall_seconds=30,
        max_gpu_seconds=30,
    )


def _declare_b_test_adapter(run_dir: Path, session_id: str) -> None:
    manifest_path = run_dir / "repos" / "source_micro" / "autoad_executor_adapter.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evaluation_commands"] = {
        "b_dev": {"args": ["run.py"], "metrics_output": "metrics.json"},
        "b_test": {"args": ["run.py"], "metrics_output": "metrics.json"},
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    binding_path = run_dir / "task_bridge" / "execution_repository_binding.json"
    binding = ExecutionRepositoryBinding.model_validate_json(binding_path.read_text(encoding="utf-8"))
    updated_binding = binding.model_copy(update={"adapter_manifest_sha256": sha256_file(manifest_path)})
    binding_path.write_text(updated_binding.model_dump_json(), encoding="utf-8")
    _git(manifest_path.parent, "add", "autoad_executor_adapter.json")
    _git(manifest_path.parent, "commit", "-m", "declare fixture b-test command")
    session = ExperimentSessionStore().load(run_dir, session_id)
    assert session is not None
    ExperimentSessionStore._write_unlocked(
        run_dir / "experiments" / "sessions" / f"{session_id}.json",
        session.model_copy(update={"execution_repository_binding_sha256": canonical_sha256(updated_binding)}),
    )


def test_baseline_control_freezes_server_owned_attempt_and_projects_ready(tmp_path: Path):
    run_dir = tmp_path / "run"
    session = _ready_session(run_dir)

    first = BaselineControlService().start(run_dir, session_id=session.session_id, contract_input=_contract())
    replay = BaselineControlService().start(run_dir, session_id=session.session_id, contract_input=_contract())

    assert first.started.disposition == "created"
    assert replay.started.disposition == "reused"
    assert first.started.attempt.attempt_id == replay.started.attempt.attempt_id
    assert first.started.attempt.command_plan.cwd.startswith("experiments/executor_worktrees/baseline-")
    assert (run_dir / first.evaluation_contract_ref).is_file()
    assert (run_dir / first.execution_inputs_ref).is_file()
    assert len(load_pipeline_jobs(run_dir)) == 1

    for _ in range(100):
        _process_pending_jobs(run_dir)
        attempt = ExperimentAttemptStore().load(run_dir, first.started.attempt.attempt_id)
        if attempt is not None and attempt.runtime_status == "COMPLETED":
            break
        time.sleep(0.02)

    attempt = ExperimentAttemptStore().load(run_dir, first.started.attempt.attempt_id)
    projected = ExperimentSessionStore().load(run_dir, session.session_id)
    assert attempt is not None and attempt.runtime_status == "COMPLETED"
    assert projected is not None and projected.status == "READY" and projected.baseline_status == "completed"
    assert json.loads((run_dir / "attempts" / attempt.attempt_id / "outcome_card.json").read_text())["metrics"] == {"score": 0.8}


def test_baseline_control_refuses_metric_that_was_not_confirmed(tmp_path: Path):
    run_dir = tmp_path / "run"
    session = _ready_session(run_dir)

    with pytest.raises(ValueError, match="user-confirmed task metrics"):
        BaselineControlService().start(
            run_dir,
            session_id=session.session_id,
            contract_input=_contract().model_copy(update={"primary_metric": "other"}),
        )

    assert not (run_dir / "experiments" / "attempts").exists()
    assert load_pipeline_jobs(run_dir) == []


def test_baseline_control_queues_explicit_b_test_baseline_and_waits_for_both(tmp_path: Path):
    run_dir = tmp_path / "run"
    session = _ready_session(run_dir)
    _declare_b_test_adapter(run_dir, session.session_id)
    started = BaselineControlService().start(run_dir, session_id=session.session_id, contract_input=_contract())
    assert started.b_test_started is not None
    assert started.b_test_started.attempt.command_plan.command_id == "generic_python_b_test"
    for _ in range(100):
        _process_pending_jobs(run_dir)
        projected = ExperimentSessionStore().load(run_dir, session.session_id)
        if projected is not None and projected.status == "READY":
            break
        time.sleep(0.02)
    projected = ExperimentSessionStore().load(run_dir, session.session_id)
    assert projected is not None and projected.status == "READY"


def test_candidate_confirmation_runs_b_test_and_registers_immutable_candidate(tmp_path: Path):
    run_dir = tmp_path / "run"
    session = _ready_session(run_dir)
    _declare_b_test_adapter(run_dir, session.session_id)
    baseline = BaselineControlService().start(run_dir, session_id=session.session_id, contract_input=_contract())
    for _ in range(100):
        _process_pending_jobs(run_dir)
        projected = ExperimentSessionStore().load(run_dir, session.session_id)
        if projected is not None and projected.status == "READY":
            break
        time.sleep(0.02)
    tree, _ = IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)
    IdeaTreeStore().add_node(run_dir, session_id=session.session_id, expected_revision=tree.revision, idempotency_key="idea-confirm", parent_id="idea_000000", mechanism="score change", hypothesis="raise score", observable="score", grounding=[], expected_cost="low")
    before = "Path(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({'score': 0.8}))\n"
    candidate = CandidateControlService().start(
        run_dir,
        session_id=session.session_id,
        value=CandidateLaunchInput(
            idempotency_key="candidate:confirm", comparison_seed=1,
            intervention_contract=InterventionContract(idea_id="idea_000001", mechanism="score change", hypothesis="raise score", target_modules=["run.py"], allowed_paths=["run.py"], forbidden_paths=["evaluate.py"], allowed_parameters=["score"], time_budget=30),
            approved_proposal=ExecutorProposal(edits=[SearchReplaceEdit(path="run.py", search=before, replace=before.replace("0.8", "0.9"))], changed_symbols=["score"], confidence=1),
        ),
    )
    assert candidate.attempt is not None
    candidate_attempt_id = str(candidate.attempt["attempt_id"])
    for _ in range(100):
        _process_pending_jobs(run_dir)
        attempt = ExperimentAttemptStore().load(run_dir, candidate_attempt_id)
        if attempt is not None and attempt.runtime_status == "COMPLETED":
            break
        time.sleep(0.02)
    confirmation = CandidateConfirmationService().start(
        run_dir,
        session_id=session.session_id,
        value=CandidateConfirmationInput(candidate_attempt_id=candidate_attempt_id, noise_threshold=0.01, idempotency_key="confirm:idea-1"),
    )
    confirmation_id = confirmation.started.attempt.attempt_id
    for _ in range(100):
        _process_pending_jobs(run_dir)
        if (run_dir / "experiments" / "champions" / "candidates" / f"candidate_{int(candidate_attempt_id.rsplit('_', 1)[1]):06d}.json").is_file():
            break
        time.sleep(0.02)
    snapshot = CandidateRegistry().load_candidate(run_dir, f"candidate_{int(candidate_attempt_id.rsplit('_', 1)[1]):06d}")
    assert snapshot.attempt_id == candidate_attempt_id
    assert snapshot.b_test_passed
    assert snapshot.b_test_evidence_ref == f"attempts/{confirmation_id}/scientific_assessment.json"
    replay = CandidateConfirmationService().start(
        run_dir,
        session_id=session.session_id,
        value=CandidateConfirmationInput(candidate_attempt_id=candidate_attempt_id, noise_threshold=0.01, idempotency_key="confirm:idea-1"),
    )
    assert replay.started.disposition == "reused"
    assert replay.candidate_snapshot_ref == f"experiments/champions/candidates/{snapshot.candidate_id}.json"
    promoted = PromotionControlService().promote(run_dir, value=PromotionInput(candidate_id=snapshot.candidate_id, approved_by="fixture-user"))
    assert promoted.champion_event["candidate_id"] == snapshot.candidate_id
    assert CandidateRegistry().current_by_contract(run_dir)[snapshot.evaluation_contract_hash].candidate_id == snapshot.candidate_id
    source = run_dir / "repos" / "source_micro"
    assert "0.9" in (source / "run.py").read_text(encoding="utf-8")


def test_baseline_control_rejects_conflicting_replay_without_a_new_job(tmp_path: Path):
    run_dir = tmp_path / "run"
    session = _ready_session(run_dir)
    first = BaselineControlService().start(run_dir, session_id=session.session_id, contract_input=_contract())

    with pytest.raises(ValueError, match="idempotency_conflict"):
        BaselineControlService().start(
            run_dir,
            session_id=session.session_id,
            contract_input=_contract().model_copy(update={"seeds": [2]}),
        )

    assert len(load_pipeline_jobs(run_dir)) == 1
    assert ExperimentAttemptStore().list_for_session(run_dir, session_id=session.session_id)[0].attempt_id == first.started.attempt.attempt_id


def test_baseline_control_rejects_unacquired_selected_source(tmp_path: Path):
    run_dir = tmp_path / "run"
    session = _ready_session(run_dir)
    from autoad_researcher.ui.sources import append_source_ref
    append_source_ref(
        run_dir, kind="dataset", user_label="pending fixture", stored_path=None,
        status="user_provided_not_ingested", source_id="dataset_pending", intake_status="pending",
    )
    with pytest.raises(ValueError, match="selected input source is not acquired"):
        BaselineControlService().start(
            run_dir, session_id=session.session_id,
            contract_input=_contract().model_copy(update={"dataset_source_ids": ["dataset_pending"]}),
        )
    assert load_pipeline_jobs(run_dir) == []


def test_finalizer_rejects_a_split_changed_after_baseline_freeze(tmp_path: Path):
    run_dir = tmp_path / "run"
    session = _ready_session(run_dir)
    started = BaselineControlService().start(run_dir, session_id=session.session_id, contract_input=_contract())
    (run_dir / "inputs" / "dev.json").write_text('{"changed": true}\n', encoding="utf-8")
    for _ in range(100):
        _process_pending_jobs(run_dir)
        attempt = ExperimentAttemptStore().load(run_dir, started.started.attempt.attempt_id)
        if attempt is not None and attempt.runtime_status == "COMPLETED":
            break
        time.sleep(0.02)
    card = json.loads((run_dir / "attempts" / started.started.attempt.attempt_id / "outcome_card.json").read_text())
    assert card["attempt_category"] == "protocol_violated"
    assert "inputs/dev.json" in " ".join(card["protocol_errors"])


def test_candidate_control_derives_execution_from_completed_baseline(tmp_path: Path):
    run_dir = tmp_path / "run"
    session = _ready_session(run_dir)
    baseline = BaselineControlService().start(run_dir, session_id=session.session_id, contract_input=_contract())
    for _ in range(100):
        _process_pending_jobs(run_dir)
        attempt = ExperimentAttemptStore().load(run_dir, baseline.started.attempt.attempt_id)
        if attempt is not None and attempt.runtime_status == "COMPLETED":
            break
        time.sleep(0.02)
    tree, _ = IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)
    tree = IdeaTreeStore().add_node(
        run_dir, session_id=session.session_id, expected_revision=tree.revision, idempotency_key="idea-1",
        parent_id="idea_000000", mechanism="score change", hypothesis="raise score", observable="score",
        grounding=[], expected_cost="low",
    )
    before = "Path(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({'score': 0.8}))\n"
    after = "Path(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({'score': 0.9}))\n"
    started = CandidateControlService().start(
        run_dir,
        session_id=session.session_id,
        value=CandidateLaunchInput(
            idempotency_key="candidate:idea-1",
            comparison_seed=1,
            intervention_contract=InterventionContract(
                idea_id="idea_000001", mechanism="score change", hypothesis="raise score", target_modules=["run.py"],
                allowed_paths=["run.py"], forbidden_paths=["evaluate.py"], allowed_parameters=["score"], time_budget=30,
            ),
            approved_proposal=ExecutorProposal(
                edits=[SearchReplaceEdit(path="run.py", search=before, replace=after)], changed_symbols=["score"], confidence=1,
            ),
        ),
    )
    assert started.status == "queued" and started.attempt is not None
    assert started.attempt["command_plan"]["cwd"] != baseline.started.attempt.command_plan.cwd
    for _ in range(100):
        _process_pending_jobs(run_dir)
        attempt = ExperimentAttemptStore().load(run_dir, str(started.attempt["attempt_id"]))
        if attempt is not None and attempt.runtime_status == "COMPLETED":
            break
        time.sleep(0.02)
    assessment = ScientificAssessmentService().effective_assessment(run_dir, attempt_id=str(started.attempt["attempt_id"]))
    assert assessment.scientific_effect == "IMPROVEMENT"
    replay = CandidateControlService().start(
        run_dir, session_id=session.session_id,
        value=CandidateLaunchInput(
            idempotency_key="candidate:idea-1", comparison_seed=1,
            intervention_contract=InterventionContract(idea_id="idea_000001", mechanism="score change", hypothesis="raise score", target_modules=["run.py"], allowed_paths=["run.py"], forbidden_paths=["evaluate.py"], allowed_parameters=["score"], time_budget=30),
            approved_proposal=ExecutorProposal(edits=[SearchReplaceEdit(path="run.py", search=before, replace=after)], changed_symbols=["score"], confidence=1),
        ),
    )
    assert replay.status == "reused" and replay.attempt is not None and replay.attempt["attempt_id"] == started.attempt["attempt_id"]


def test_candidate_control_rejects_conflicting_replay_without_a_new_job(tmp_path: Path):
    run_dir = tmp_path / "run"
    session = _ready_session(run_dir)
    baseline = BaselineControlService().start(run_dir, session_id=session.session_id, contract_input=_contract())
    for _ in range(100):
        _process_pending_jobs(run_dir)
        attempt = ExperimentAttemptStore().load(run_dir, baseline.started.attempt.attempt_id)
        if attempt is not None and attempt.runtime_status == "COMPLETED":
            break
        time.sleep(0.02)
    tree, _ = IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)
    IdeaTreeStore().add_node(run_dir, session_id=session.session_id, expected_revision=tree.revision, idempotency_key="idea-conflict", parent_id="idea_000000", mechanism="score change", hypothesis="raise score", observable="score", grounding=[], expected_cost="low")
    before = "Path(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({'score': 0.8}))\n"
    candidate = CandidateLaunchInput(
        idempotency_key="candidate:conflict", comparison_seed=1,
        intervention_contract=InterventionContract(idea_id="idea_000001", mechanism="score change", hypothesis="raise score", target_modules=["run.py"], allowed_paths=["run.py"], forbidden_paths=["evaluate.py"], allowed_parameters=["score"], time_budget=30),
        approved_proposal=ExecutorProposal(edits=[SearchReplaceEdit(path="run.py", search=before, replace=before.replace("0.8", "0.9"))], changed_symbols=["score"], confidence=1),
    )
    first = CandidateControlService().start(run_dir, session_id=session.session_id, value=candidate)
    with pytest.raises(ValueError, match="idempotency_conflict"):
        CandidateControlService().start(run_dir, session_id=session.session_id, value=candidate.model_copy(update={"comparison_seed": 2}))
    assert len(load_pipeline_jobs(run_dir)) == 2
    assert first.attempt is not None
