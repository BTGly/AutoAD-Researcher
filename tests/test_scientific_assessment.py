from pathlib import Path

from autoad_researcher.experiment.evaluation_contract import EvaluationContract, EvaluationMetric, EvaluationResourceBudget
from autoad_researcher.experiment.executor_agent import ExecutorSummary
from autoad_researcher.experiment.finalizer import OutcomeCard
from autoad_researcher.experiment.scientific_assessment import (
    EffectiveScientificAssessment,
    ScientificAssessmentInputsStore,
    ScientificAssessmentService,
    ScientificEvaluationInputs,
    load_declared_metric_values,
)
from autoad_researcher.experiment.validity import ComparisonIdentity


def _identity() -> ComparisonIdentity:
    return ComparisonIdentity(
        dataset_identity="dataset-v1",
        split_identity="split-v1",
        seed=1,
        checkpoint_selection="best-primary",
        command_sha256="b" * 64,
        metric_implementation_refs=["metric.py"],
        evaluation_contract_sha256="c" * 64,
        outputs_complete=True,
    )


def _contract() -> EvaluationContract:
    return EvaluationContract(
        contract_id="evaluation_contract_000001",
        session_id="session_000001",
        revision=0,
        baseline_commit="a" * 40,
        dataset_identity="dataset-v1",
        split_identity="split-v1",
        b_dev_ref="b_dev.json",
        b_test_ref="b_test.json",
        category_set=["bottle"],
        metrics=[EvaluationMetric(name="score", direction="maximize", implementation_ref="metric.py")],
        primary_metric="score",
        aggregation="mean",
        seeds=[1],
        checkpoint_selection="best-primary",
        resource_budget=EvaluationResourceBudget(max_wall_seconds=10, max_gpu_seconds=10),
        protected_paths=["metric.py"],
    )


def test_assessment_uses_explicit_inputs_and_executor_artifacts(tmp_path: Path):
    contract_path = tmp_path / "evaluation_contract.json"
    contract_path.write_text(_contract().model_dump_json(), encoding="utf-8")
    attempt_dir = tmp_path / "attempts" / "attempt_000001"
    attempt_dir.mkdir(parents=True)
    card = OutcomeCard(
        attempt_id="attempt_000001",
        runtime_status="COMPLETED",
        attempt_category="scientifically_evaluable",
        execution_result_ref="attempts/attempt_000001/execution_result.json",
        metrics={"score": 0.9},
        evaluation_contract_ref="evaluation_contract.json",
        protocol_valid=True,
        execution_status="COMPLETED",
        metrics_parsed=True,
        protocol_intact=True,
        evaluation_status="NON_COMPARABLE",
    )
    (attempt_dir / "outcome_card.json").write_text(card.model_dump_json(), encoding="utf-8")
    (attempt_dir / "executor_summary.json").write_text(
        ExecutorSummary(
            status="completed",
            model_calls=1,
            steps=1,
            changed_files=["model.py"],
            changed_symbols=["scale"],
            confidence=1,
        ).model_dump_json(),
        encoding="utf-8",
    )
    (attempt_dir / "patch.diff").write_text("diff --git a/model.py b/model.py\n", encoding="utf-8")
    inputs = ScientificEvaluationInputs(
        baseline_metrics={"score": 0.8},
        candidate_identity=_identity(),
        baseline_identity=_identity(),
    )
    ScientificAssessmentInputsStore().save(attempt_dir, inputs)

    service = ScientificAssessmentService()
    assessment = service.assess(tmp_path, attempt_id="attempt_000001")
    assert assessment.patch_applied
    assert assessment.smoke_passed
    assert assessment.evaluation_status == "COMPARABLE"
    assert assessment.scientific_effect == "IMPROVEMENT"
    assert round(assessment.primary_delta or 0, 6) == 0.1
    enriched = service.assessed_card(tmp_path, attempt_id="attempt_000001")
    assert enriched.scientific_effect == "IMPROVEMENT"
    assert enriched.patch_applied is True
    assert service.assess(tmp_path, attempt_id="attempt_000001") == assessment


def test_assessment_does_not_claim_patch_or_smoke_without_executor_evidence(tmp_path: Path):
    contract_path = tmp_path / "evaluation_contract.json"
    contract_path.write_text(_contract().model_dump_json(), encoding="utf-8")
    attempt_dir = tmp_path / "attempts" / "attempt_000001"
    attempt_dir.mkdir(parents=True)
    card = OutcomeCard(
        attempt_id="attempt_000001",
        runtime_status="COMPLETED",
        attempt_category="scientifically_evaluable",
        execution_result_ref="attempts/attempt_000001/execution_result.json",
        metrics={"score": 0.9},
        evaluation_contract_ref="evaluation_contract.json",
        protocol_valid=True,
        execution_status="COMPLETED",
        metrics_parsed=True,
        protocol_intact=True,
        evaluation_status="NON_COMPARABLE",
    )
    (attempt_dir / "outcome_card.json").write_text(card.model_dump_json(), encoding="utf-8")
    ScientificAssessmentInputsStore().save(
        attempt_dir,
        ScientificEvaluationInputs(
            baseline_metrics={"score": 0.8},
            candidate_identity=_identity(),
            baseline_identity=_identity(),
        ),
    )
    assessment = ScientificAssessmentService().assess(tmp_path, attempt_id="attempt_000001")
    assert not assessment.patch_applied
    assert not assessment.smoke_passed
    assert assessment.scientific_effect is None


def test_effective_assessment_reconciles_raw_card_without_rewriting_it(tmp_path: Path):
    contract_path = tmp_path / "evaluation_contract.json"
    contract_path.write_text(_contract().model_dump_json(), encoding="utf-8")
    attempt_dir = tmp_path / "attempts" / "attempt_000001"
    attempt_dir.mkdir(parents=True)
    raw = OutcomeCard(
        attempt_id="attempt_000001", runtime_status="COMPLETED",
        attempt_category="scientifically_evaluable",
        execution_result_ref="attempts/attempt_000001/execution_result.json",
        metrics={"score": 0.9}, evaluation_contract_ref="evaluation_contract.json",
        protocol_valid=True, execution_status="COMPLETED", metrics_parsed=True,
        protocol_intact=True, evaluation_status="NON_COMPARABLE",
    )
    (attempt_dir / "outcome_card.json").write_text(raw.model_dump_json(), encoding="utf-8")
    (attempt_dir / "executor_summary.json").write_text(
        ExecutorSummary(status="completed", model_calls=1, steps=1, changed_files=["model.py"], changed_symbols=["scale"], confidence=1).model_dump_json(),
        encoding="utf-8",
    )
    (attempt_dir / "patch.diff").write_text("diff --git a/model.py b/model.py\n", encoding="utf-8")
    ScientificAssessmentInputsStore().save(
        attempt_dir,
        ScientificEvaluationInputs(baseline_metrics={"score": 0.8}, candidate_identity=_identity(), baseline_identity=_identity()),
    )
    effective = ScientificAssessmentService().effective_assessment(tmp_path, attempt_id="attempt_000001")
    assert isinstance(effective, EffectiveScientificAssessment)
    assert effective.evaluation_status == "COMPARABLE"
    assert OutcomeCard.model_validate_json((attempt_dir / "outcome_card.json").read_text()).evaluation_status == "NON_COMPARABLE"
    assert (attempt_dir / "assessment_reconciliation.json").is_file()


def test_declared_metric_values_ignore_outcome_metadata(tmp_path: Path):
    contract = _contract().model_copy(update={
        "metrics": [
            EvaluationMetric(name="score", direction="maximize", implementation_ref="metric.py"),
            EvaluationMetric(name="loss", direction="minimize", implementation_ref="metric.py"),
        ],
        "guardrails": ["loss"],
    })
    (tmp_path / "evaluation_contract.json").write_text(contract.model_dump_json(), encoding="utf-8")
    attempt_dir = tmp_path / "attempts" / "attempt_000001"
    attempt_dir.mkdir(parents=True)
    card = OutcomeCard(
        attempt_id="attempt_000001",
        runtime_status="COMPLETED",
        attempt_category="scientifically_evaluable",
        execution_result_ref="execution_result.json",
        metrics={"score": 0.9, "loss": 0.2, "split": "b_dev", "seed": 7, "sample_count": 10},
        evaluation_contract_ref="evaluation_contract.json",
        protocol_valid=True,
        execution_status="COMPLETED",
        metrics_parsed=True,
        protocol_intact=True,
        evaluation_status="NON_COMPARABLE",
    )
    (attempt_dir / "outcome_card.json").write_text(card.model_dump_json(), encoding="utf-8")

    assert load_declared_metric_values(tmp_path, attempt_id="attempt_000001") == {"score": 0.9, "loss": 0.2}


def _candidate_attempt(run_dir: Path, attempt_id: str, score: float, *, patch: str) -> None:
    directory = run_dir / "attempts" / attempt_id
    directory.mkdir(parents=True, exist_ok=True)
    card = OutcomeCard(
        attempt_id=attempt_id,
        runtime_status="COMPLETED",
        attempt_category="scientifically_evaluable",
        execution_result_ref=f"attempts/{attempt_id}/execution_result.json",
        metrics={"score": score},
        evaluation_contract_ref="evaluation_contract.json",
        protocol_valid=True,
        execution_status="COMPLETED",
        metrics_parsed=True,
        protocol_intact=True,
        evaluation_status="COMPARABLE",
    )
    (directory / "outcome_card.json").write_text(card.model_dump_json(), encoding="utf-8")
    (directory / "candidate_request.json").write_text("{}\n", encoding="utf-8")
    (directory / "final_patch.diff").write_text(patch, encoding="utf-8")
    (directory / "patch.diff").write_text(patch, encoding="utf-8")
    (directory / "executor_summary.json").write_text(
        ExecutorSummary(
            status="completed",
            model_calls=1,
            steps=1,
            changed_files=["model.py"],
            changed_symbols=["score"],
            confidence=1,
        ).model_dump_json(),
        encoding="utf-8",
    )
    ScientificAssessmentInputsStore().save(
        directory,
        ScientificEvaluationInputs(
            baseline_metrics={"score": 0.8},
            candidate_identity=_identity(),
            baseline_identity=_identity(),
        ),
    )


def test_assessment_suppresses_improvement_when_same_patch_metrics_vary(tmp_path: Path):
    (tmp_path / "evaluation_contract.json").write_text(_contract().model_dump_json(), encoding="utf-8")
    _candidate_attempt(tmp_path, "attempt_000001", 0.9, patch="same patch\n")
    _candidate_attempt(tmp_path, "attempt_000002", 0.85, patch="same patch\n")

    service = ScientificAssessmentService()
    first = service.assess(tmp_path, attempt_id="attempt_000001")
    assessment = service.assess(tmp_path, attempt_id="attempt_000002")

    assert service.assess(tmp_path, attempt_id="attempt_000001") == first
    assert assessment.reproducibility_status == "not_reproducible"
    assert assessment.scientific_effect is None
    assert assessment.reproducibility_ref == "attempts/attempt_000002/reproducibility.json"
    evidence = (tmp_path / assessment.reproducibility_ref).read_text(encoding="utf-8")
    assert "fix the random seed" in evidence


def test_assessment_accepts_exact_same_patch_metrics_as_reproducible(tmp_path: Path):
    (tmp_path / "evaluation_contract.json").write_text(_contract().model_dump_json(), encoding="utf-8")
    _candidate_attempt(tmp_path, "attempt_000001", 0.9, patch="same patch\n")
    _candidate_attempt(tmp_path, "attempt_000002", 0.9, patch="same patch\n")

    assessment = ScientificAssessmentService().assess(tmp_path, attempt_id="attempt_000002")

    assert assessment.reproducibility_status == "reproducible"
    assert assessment.scientific_effect == "IMPROVEMENT"
