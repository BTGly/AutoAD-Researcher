import pytest

from autoad_researcher.experiment.evaluation_contract import EvaluationContract, EvaluationMetric, EvaluationResourceBudget
from autoad_researcher.experiment.validity import ComparisonIdentity, ImplementationEvidence, comparable, scientific_effect


def test_comparability_and_effect_require_all_deterministic_gates():
    contract = EvaluationContract(contract_id="evaluation_contract_000001", session_id="session", revision=0, baseline_commit="a" * 40, dataset_identity="dataset", split_identity="split", b_dev_ref="dev.json", b_test_ref="test.json", category_set=["bottle"], metrics=[EvaluationMetric(name="score", direction="maximize", implementation_ref="metric.py")], primary_metric="score", aggregation="mean", seeds=[1], checkpoint_selection="best", resource_budget=EvaluationResourceBudget(max_wall_seconds=1, max_gpu_seconds=1), protected_paths=["metric.py"])
    identity = ComparisonIdentity(dataset_identity="dataset", split_identity="split", seed=1, checkpoint_selection="best", command_sha256="b" * 64, metric_implementation_refs=["metric.py"], evaluation_contract_sha256="c" * 64, outputs_complete=True)
    assert comparable(identity, identity) == "COMPARABLE"
    effect, delta, _ = scientific_effect(candidate_metrics={"score": .9}, baseline_metrics={"score": .8}, contract=contract, evaluation_status="COMPARABLE", implementation_evidence=ImplementationEvidence(patch_applied=True, smoke_passed=True), metrics_parsed=True, protocol_intact=True)
    assert effect == "IMPROVEMENT"
    assert delta == pytest.approx(.1)
    assert scientific_effect(candidate_metrics={"score": .9}, baseline_metrics={"score": .8}, contract=contract, evaluation_status="NON_COMPARABLE", implementation_evidence=ImplementationEvidence(patch_applied=True, smoke_passed=True), metrics_parsed=True, protocol_intact=True)[0] is None


def test_effect_is_inconclusive_when_any_contract_metric_is_missing():
    contract = EvaluationContract(
        contract_id="evaluation_contract_000001",
        session_id="session",
        revision=0,
        baseline_commit="a" * 40,
        dataset_identity="dataset",
        split_identity="split",
        b_dev_ref="dev.json",
        b_test_ref="test.json",
        category_set=["bottle"],
        metrics=[
            EvaluationMetric(name="score", direction="maximize", implementation_ref="metric.py"),
            EvaluationMetric(name="f1", direction="maximize", implementation_ref="metric.py"),
        ],
        primary_metric="score",
        guardrails=["f1"],
        aggregation="mean",
        seeds=[1],
        checkpoint_selection="best",
        resource_budget=EvaluationResourceBudget(max_wall_seconds=1, max_gpu_seconds=1),
        protected_paths=["metric.py"],
    )
    effect, delta, guardrails = scientific_effect(
        candidate_metrics={"score": 0.9},
        baseline_metrics={"score": 0.8, "f1": 0.8},
        contract=contract,
        evaluation_status="COMPARABLE",
        implementation_evidence=ImplementationEvidence(patch_applied=True, smoke_passed=True),
        metrics_parsed=True,
        protocol_intact=True,
    )
    assert (effect, delta, guardrails) == ("INCONCLUSIVE", None, {})
