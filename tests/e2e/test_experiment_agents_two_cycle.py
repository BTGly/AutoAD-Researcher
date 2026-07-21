from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.experiment.assessed_context import AssessedScientificCoordinatorContextBuilder
from autoad_researcher.experiment.attempt_service import ExperimentAttemptService
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.cognition import CognitiveCommitStore
from autoad_researcher.experiment.convergence import ConvergenceAttempt, ConvergenceMonitor
from autoad_researcher.experiment.coordinator import CompactCycleService, CycleDecision
from autoad_researcher.experiment.evaluation_contract import (
    EvaluationContract,
    EvaluationContractStore,
    EvaluationMetric,
    EvaluationResourceBudget,
    freeze_protected_artifacts,
)
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter, ExecutorAdapterInputs
from autoad_researcher.experiment.executor_agent import ExecutorProposal
from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.experiment.executor_handoff import ExecutorHandoffRequest
from autoad_researcher.experiment.finalizer import OutcomeCard, ProtectedArtifactHashes
from autoad_researcher.experiment.idea_tree import IdeaTreeMutation, IdeaTreeStore
from autoad_researcher.experiment.noise_floor import NoiseFloorStore, calibrate_noise_floor
from autoad_researcher.experiment.patch_protocol import SearchReplaceEdit
from autoad_researcher.experiment.promotion import (
    CandidateRegistry,
    CandidateSnapshot,
    DecisionEngine,
    PromotionApproval,
    PromotionService,
)
from autoad_researcher.experiment.reflection import (
    DerivedHypothesis,
    ReflectionResult,
    ReflectionService,
    ReflectionTrigger,
)
from autoad_researcher.experiment.scientific_assessment import (
    ScientificAssessmentInputsStore,
    ScientificAssessmentService,
    ScientificEvaluationInputs,
    ScientificExecutorHandoffService,
)
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.experiment.stop_policy import StopInputs, StopPolicy
from autoad_researcher.experiment.validity import ComparisonIdentity
from autoad_researcher.worker.main import _process_pending_jobs


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()


def _repository(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "fixture@example.invalid")
    _git(path, "config", "user.name", "fixture")
    (path / "evaluate.py").write_text("protected = True\n", encoding="utf-8")
    (path / "run.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({'score': 0.8}))\n",
        encoding="utf-8",
    )
    (path / "autoad_executor_adapter.json").write_text(
        json.dumps(
            {
                "adapter_id": "generic_python",
                "entrypoint": "run.py",
                "smoke_argv": [sys.executable, "run.py"],
                "metrics_output": "metrics.json",
                "allowed_paths": ["run.py"],
                "protected_paths": ["evaluate.py"],
                "activation_evidence": "unverified",
            }
        ),
        encoding="utf-8",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "fixture baseline")
    return path


def _process_until_terminal(run_dir: Path, attempt_id: str) -> None:
    for _ in range(100):
        _process_pending_jobs(run_dir)
        attempt = ExperimentAttemptStore().load(run_dir, attempt_id)
        if attempt is not None and attempt.runtime_status in {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"}:
            return
        time.sleep(0.02)
    raise AssertionError(f"Attempt did not terminate: {attempt_id}")


def _semantic_command_identity() -> str:
    return canonical_sha256(
        {
            "program": sys.executable,
            "args": ["run.py"],
            "expected_outputs": ["metrics.json"],
            "network": False,
        }
    )


def _comparison_identity(contract_sha: str) -> ComparisonIdentity:
    return ComparisonIdentity(
        dataset_identity="fixture-dataset-v1",
        split_identity="fixture-split-v1",
        seed=1,
        checkpoint_selection="best-primary",
        command_sha256=_semantic_command_identity(),
        metric_implementation_refs=["metric.py"],
        evaluation_contract_sha256=contract_sha,
        outputs_complete=True,
    )


def _proposal(score: float):
    before = "Path(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({'score': 0.8}))\n"
    after = f"Path(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({{'score': {score}}}))\n"
    return lambda _tools: ExecutorProposal(
        edits=[SearchReplaceEdit(path="run.py", search=before, replace=after)],
        changed_symbols=["score"],
        confidence=1,
    )


def _handoff_request(
    *,
    session_id: str,
    repository: Path,
    idempotency_key: str,
    contract_ref: str,
    contract_sha: str,
    protected_ref: str,
    protected_sha: str,
    base_commit: str = "HEAD",
    job_type: str = "experiment_attempt",
) -> ExecutorHandoffRequest:
    return ExecutorHandoffRequest(
        session_id=session_id,
        job_type=job_type,
        idempotency_key=idempotency_key,
        repository_path=repository,
        base_commit=base_commit,
        environment_snapshot_ref="environment/snapshot.json",
        adapter_inputs=ExecutorAdapterInputs(
            run_id="two-cycle",
            worktree_ref="explicitly-replaced-by-handoff",
            repository_fingerprint="fixture",
            environment_sha256="b" * 64,
            dataset_manifest_sha256="c" * 64,
            asset_manifest_sha256="d" * 64,
            python_executable=sys.executable,
        ),
        intervention_contract=InterventionContract(
            idea_id="idea_000001",
            mechanism="controlled parameter",
            hypothesis="raising the deterministic score improves the primary metric",
            target_modules=["run.py"],
            allowed_paths=["run.py"],
            forbidden_paths=["evaluate.py"],
            max_repairs=1,
            time_budget=30,
        ),
        job_timeout_sec=30,
        evaluation_contract_ref=contract_ref,
        evaluation_contract_sha256=contract_sha,
        protected_artifact_report_ref=protected_ref,
        protected_artifact_report_sha256=protected_sha,
    )


def test_two_result_driven_cycles_reach_confirmed_champion(tmp_path: Path):
    run_dir = tmp_path / "run"
    repository = _repository(run_dir / "baseline_repo")
    session_store = ExperimentSessionStore()
    session, _ = session_store.create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash="a" * 64,
        execution_mode="agent_assisted_after_approval",
        repository_ref="baseline_repo",
        budget={"gpu_seconds": 100, "cognitive_calls": 20},
    )
    session_store.update_environment_state(
        run_dir,
        session_id=session.session_id,
        status="READY_FOR_BASELINE",
        environment_status="ready",
        readiness_status="ready",
        readiness_blockers=[],
        repository_ref="baseline_repo",
        environment_snapshot_ref="environment/snapshot.json",
    )
    IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)

    (run_dir / "metric.py").write_text("metric = 'score'\n", encoding="utf-8")
    (run_dir / "splits").mkdir(parents=True)
    (run_dir / "splits" / "b_dev.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "splits" / "b_test.json").write_text("{}\n", encoding="utf-8")
    contract = EvaluationContract(
        contract_id="evaluation_contract_000001",
        session_id=session.session_id,
        revision=0,
        baseline_commit=_git(repository, "rev-parse", "HEAD"),
        dataset_identity="fixture-dataset-v1",
        split_identity="fixture-split-v1",
        b_dev_ref="splits/b_dev.json",
        b_test_ref="splits/b_test.json",
        category_set=["fixture"],
        metrics=[EvaluationMetric(name="score", direction="maximize", implementation_ref="metric.py")],
        primary_metric="score",
        aggregation="mean",
        seeds=[1, 2, 3, 4, 5],
        checkpoint_selection="best-primary",
        resource_budget=EvaluationResourceBudget(max_wall_seconds=120, max_gpu_seconds=120),
        protected_paths=["metric.py"],
    )
    frozen = EvaluationContractStore().freeze(run_dir, contract=contract)
    session_store.bind_evaluation_contract(
        run_dir,
        session_id=session.session_id,
        evaluation_contract_ref=frozen.ref,
        evaluation_contract_sha256=frozen.sha256,
        evaluation_contract_revision=0,
    )
    protected_path = run_dir / "protected_hashes.json"
    protected_path.write_text(
        ProtectedArtifactHashes(hashes=freeze_protected_artifacts(run_dir, contract.protected_paths)).model_dump_json(),
        encoding="utf-8",
    )
    protected_ref = str(protected_path.relative_to(run_dir))
    protected_sha = sha256_file(protected_path)

    adapter = ExecutorAdapter()
    adapter_result = adapter.inspect(repository)
    baseline_plan, baseline_refs = adapter.build_execution(
        adapter_result,
        ExecutorAdapterInputs(
            run_id=run_dir.name,
            worktree_ref="baseline_repo",
            repository_fingerprint="fixture",
            environment_sha256="b" * 64,
            dataset_manifest_sha256="c" * 64,
            asset_manifest_sha256="d" * 64,
            python_executable=sys.executable,
        ),
    )
    baseline = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session.session_id,
        job_type="experiment_baseline",
        idempotency_key="two-cycle:baseline",
        command_plan=baseline_plan,
        input_refs=baseline_refs,
        job_timeout_sec=30,
        evaluation_contract_ref=frozen.ref,
        evaluation_contract_sha256=frozen.sha256,
        protected_artifact_report_ref=protected_ref,
        protected_artifact_report_sha256=protected_sha,
    )
    _process_until_terminal(run_dir, baseline.attempt.attempt_id)
    baseline_card = OutcomeCard.model_validate_json(
        (run_dir / "attempts" / baseline.attempt.attempt_id / "outcome_card.json").read_text(encoding="utf-8")
    )
    assert baseline_card.metrics == {"score": 0.8}
    session_store.update_environment_state(
        run_dir,
        session_id=session.session_id,
        status="READY",
        environment_status="ready",
        readiness_status="ready",
        readiness_blockers=[],
    )
    noise = calibrate_noise_floor(
        session_id=session.session_id,
        metric="score",
        category="fixture",
        samples=[0.8, 0.802, 0.798, 0.801, 0.799],
    )
    NoiseFloorStore().save(run_dir, noise)
    assert noise.status == "LOCKED"
    assert noise.threshold is not None

    first_cycle = CompactCycleService().run(
        run_dir,
        session_id=session.session_id,
        cycle_id="cycle_000001",
        observation="baseline is stable",
        ideation_focus="controlled parameter",
        decision_provider=lambda _context: CycleDecision(
            observation="baseline is stable",
            comparison="no candidate has run yet",
            hypothesis_verdict="test one controlled parameter",
            keep_why="retain the frozen baseline and protocol",
            failure_why="no failure observed",
            confidence=0.8,
            uncertainty="single synthetic task",
            next_action="add_child",
            target_node_id="idea_000000",
            mutations=[
                IdeaTreeMutation(
                    kind="add_child",
                    parent_id="idea_000000",
                    mechanism="controlled parameter",
                    hypothesis="raising score improves the primary metric",
                    observable="score",
                    expected_cost="low",
                )
            ],
        ),
        model_profile="fixture",
        prompt_version="coordinator-v1",
    )
    assert first_cycle.tree.node("idea_000001").parent_id == "idea_000000"

    identity = _comparison_identity(frozen.sha256)
    scientific_inputs = ScientificEvaluationInputs(
        baseline_metrics={"score": 0.8},
        candidate_identity=identity,
        baseline_identity=identity,
    )
    first = ScientificExecutorHandoffService().handoff(
        run_dir,
        request=_handoff_request(
            session_id=session.session_id,
            repository=repository,
            idempotency_key="two-cycle:idea-1",
            contract_ref=frozen.ref,
            contract_sha=frozen.sha256,
            protected_ref=protected_ref,
            protected_sha=protected_sha,
        ),
        scientific_inputs=scientific_inputs,
        proposal_provider=_proposal(0.86),
    )
    assert first.status == "queued" and first.attempt is not None
    first_attempt_id = str(first.attempt["attempt_id"])
    _process_until_terminal(run_dir, first_attempt_id)
    first_assessed = ScientificAssessmentService().assessed_card(run_dir, attempt_id=first_attempt_id)
    first_decision = DecisionEngine().decide(
        assessment=ScientificAssessmentService().effective_assessment(run_dir, attempt_id=first_attempt_id),
        phase="b_dev",
        noise_threshold=noise.threshold,
    )
    assert first_decision.action == "candidate"

    reflection = ReflectionService().run(
        run_dir,
        session_id=session.session_id,
        cycle_id="reflection_000001",
        target_node_id="idea_000001",
        triggers=[
            ReflectionTrigger(
                kind="high_value_improvement",
                rationale="first result exceeds the locked noise floor",
                evidence_refs=[f"attempts/{first_attempt_id}/scientific_assessment.json"],
            )
        ],
        outcome_refs=[f"attempts/{first_attempt_id}/outcome_card.json"],
        provider=lambda _tree, _triggers: ReflectionResult(
            observed_effect="score improved beyond the locked noise floor",
            mechanism_interpretation="the controlled scalar has measurable leverage",
            alternative_explanations=["deterministic fixture scale"],
            implementation_concerns=[],
            hypothesis_verdict="retain and test a stronger bounded child",
            keep_why="the first intervention produced a valid comparable improvement",
            failure_why="the first intervention has not yet reached the best bounded value",
            confidence=0.9,
            uncertainty="synthetic fixture only",
            reusable_property="one-variable interventions are auditable",
            derived_hypotheses=[
                DerivedHypothesis(
                    mechanism="stronger controlled parameter",
                    hypothesis="a second bounded increase produces a larger valid improvement",
                    observable="score",
                    research_axis="controlled_scale",
                    minimal_intervention="increase only the fixture score",
                    falsification="score does not exceed the first result beyond noise",
                    relationship_to_previous_ideas="direct child of the supported first intervention",
                    expected_cost="low",
                    grounding=[f"attempts/{first_attempt_id}/scientific_assessment.json"],
                )
            ],
            recommended_tree_action="derive_child",
            evidence_refs=[f"attempts/{first_attempt_id}/scientific_assessment.json"],
        ),
        model_profile="fixture",
        prompt_version="reflection-v1",
    )
    assert reflection.tree.node("idea_000002").parent_id == "idea_000001"

    second_request = _handoff_request(
        session_id=session.session_id,
        repository=repository,
        idempotency_key="two-cycle:idea-2",
        contract_ref=frozen.ref,
        contract_sha=frozen.sha256,
        protected_ref=protected_ref,
        protected_sha=protected_sha,
    )
    second_request = second_request.model_copy(
        update={
            "intervention_contract": second_request.intervention_contract.model_copy(
                update={
                    "idea_id": "idea_000002",
                    "mechanism": "stronger controlled parameter",
                    "hypothesis": "a second bounded increase produces a larger valid improvement",
                }
            )
        }
    )
    second = ScientificExecutorHandoffService().handoff(
        run_dir,
        request=second_request,
        scientific_inputs=scientific_inputs,
        proposal_provider=_proposal(0.9),
    )
    assert second.status == "queued" and second.attempt is not None and second.workspace is not None
    second_attempt_id = str(second.attempt["attempt_id"])
    _process_until_terminal(run_dir, second_attempt_id)
    second_assessed = ScientificAssessmentService().assessed_card(run_dir, attempt_id=second_attempt_id)
    second_decision = DecisionEngine().decide(
        assessment=ScientificAssessmentService().effective_assessment(run_dir, attempt_id=second_attempt_id),
        phase="b_dev",
        noise_threshold=noise.threshold,
    )
    assert second_decision.action == "candidate"
    assert (second_assessed.primary_delta or 0) > (first_assessed.primary_delta or 0)

    candidate_worktree = Path(second.workspace.worktree_path)
    _git(candidate_worktree, "add", "run.py")
    _git(candidate_worktree, "commit", "-m", "candidate second cycle")
    candidate_commit = _git(candidate_worktree, "rev-parse", "HEAD")
    confirm_adapter = adapter.inspect(candidate_worktree)
    confirm_plan, confirm_refs = adapter.build_execution(
        confirm_adapter,
        ExecutorAdapterInputs(
            run_id=run_dir.name,
            worktree_ref=str(candidate_worktree.relative_to(run_dir)),
            repository_fingerprint="fixture-candidate",
            environment_sha256="b" * 64,
            dataset_manifest_sha256="c" * 64,
            asset_manifest_sha256="d" * 64,
            python_executable=sys.executable,
        ),
    )
    confirmation = ExperimentAttemptService().create_or_get_attempt(
        run_dir,
        session_id=session.session_id,
        job_type="experiment_confirmatory",
        idempotency_key="two-cycle:b-test",
        command_plan=confirm_plan,
        input_refs=confirm_refs,
        job_timeout_sec=30,
        evaluation_contract_ref=frozen.ref,
        evaluation_contract_sha256=frozen.sha256,
        protected_artifact_report_ref=protected_ref,
        protected_artifact_report_sha256=protected_sha,
    )
    _process_until_terminal(run_dir, confirmation.attempt.attempt_id)
    confirmation_card = OutcomeCard.model_validate_json(
        (run_dir / "attempts" / confirmation.attempt.attempt_id / "outcome_card.json").read_text(encoding="utf-8")
    )
    assert confirmation_card.metrics == {"score": 0.9}
    assert confirmation_card.protocol_intact
    assert DecisionEngine().decide(
        assessment=ScientificAssessmentService().effective_assessment(run_dir, attempt_id=second_attempt_id),
        phase="b_test",
        noise_threshold=noise.threshold,
    ).action == "ready_for_promotion"

    registry = CandidateRegistry()
    candidate = CandidateSnapshot(
        candidate_id="candidate_000001",
        session_id=session.session_id,
        evaluation_contract_hash=frozen.sha256,
        idea_id="idea_000002",
        attempt_id=second_attempt_id,
        source_branch=second.workspace.branch,
        source_commit=candidate_commit,
        patch_sha256=sha256_file(run_dir / "attempts" / second_attempt_id / "patch.diff"),
        metrics_ref=f"attempts/{second_attempt_id}/metrics.json",
        resource_ref=f"attempts/{second_attempt_id}/execution_result.json",
        b_dev_evidence_ref=f"attempts/{second_attempt_id}/scientific_assessment.json",
        b_test_evidence_ref=f"attempts/{confirmation.attempt.attempt_id}/outcome_card.json",
        b_test_passed=True,
        guardrails_passed=True,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    registry.create_candidate(run_dir, candidate)
    approval = PromotionApproval(
        approval_id="approval_000001",
        candidate_id=candidate.candidate_id,
        mode="human",
        decision="approved",
        policy_snapshot_ref="experiments/policy/promotion-v1.json",
        approved_by="fixture-user",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    registry.create_approval(run_dir, approval)
    trunk_before = _git(repository, "rev-parse", "HEAD")

    def merge_candidate(snapshot: CandidateSnapshot) -> str:
        _git(repository, "merge", "--no-ff", snapshot.source_branch, "-m", "promote second-cycle candidate")
        return _git(repository, "rev-parse", "HEAD")

    event = PromotionService(registry=registry).promote_and_merge_candidate(
        run_dir,
        journal_id="promotion_000001",
        candidate_id=candidate.candidate_id,
        approval_id=approval.approval_id,
        expected_trunk_commit=trunk_before,
        current_trunk_commit=lambda: _git(repository, "rev-parse", "HEAD"),
        merge_candidate=merge_candidate,
    )
    assert event.event_type == "promoted_and_merged"
    assert registry.current_by_contract(run_dir)[frozen.sha256].candidate_id == candidate.candidate_id
    assert "0.9" in (repository / "run.py").read_text(encoding="utf-8")

    convergence = ConvergenceMonitor().evaluate(
        session_id=session.session_id,
        attempts=[
            ConvergenceAttempt(
                attempt_id=first_attempt_id,
                attempt_purpose="exploration",
                attempt_category="scientifically_evaluable",
                scientific_effect=first_assessed.scientific_effect,
                primary_delta=first_assessed.primary_delta,
                noise_threshold=noise.threshold,
            ),
            ConvergenceAttempt(
                attempt_id=second_attempt_id,
                attempt_purpose="exploration",
                attempt_category="scientifically_evaluable",
                scientific_effect=second_assessed.scientific_effect,
                primary_delta=second_assessed.primary_delta,
                noise_threshold=noise.threshold,
            ),
        ],
    )
    assert convergence.level == "none"
    assert not StopPolicy().evaluate(
        StopInputs(
            session_id=session.session_id,
            compute_budget_remaining=10,
            cognitive_calls_remaining=10,
            cognitive_tokens_remaining=100,
            wall_seconds_remaining=100,
            valid_frontier_count=1,
            consecutive_terminal_failures=0,
            convergence_alert=convergence,
        )
    ).should_stop

    context = AssessedScientificCoordinatorContextBuilder().build(run_dir, session_id=session.session_id)
    effects = {item.get("attempt_id"): item.get("scientific_effect") for item in context.outcome_cards}
    assert effects[first_attempt_id] == "IMPROVEMENT"
    assert effects[second_attempt_id] == "IMPROVEMENT"
    assert context.champion_summary[frozen.sha256]["candidate"]["candidate_id"] == "candidate_000001"
    commits = CognitiveCommitStore().load(run_dir, session_id=session.session_id)
    assert len(commits) >= 2
    assert commits[0].next_action == "add_child"
    assert commits[1].next_action == "derive_child"
