from pathlib import Path

import pytest

from autoad_researcher.experiment.evaluation_contract import (
    EvaluationContract,
    EvaluationContractStore,
    EvaluationMetric,
    EvaluationResourceBudget,
    freeze_protected_artifacts,
)
from autoad_researcher.experiment.session_store import ExperimentSessionStore


def _contract(*, revision: int = 0, contract_id: str = "evaluation_contract_000001") -> EvaluationContract:
    return EvaluationContract(
        contract_id=contract_id,
        session_id="session_fixture",
        revision=revision,
        baseline_commit="a" * 40,
        dataset_identity="mvtec-ad:fixture",
        split_identity="fixture-split-v1",
        b_dev_ref="splits/b_dev.json",
        b_test_ref="splits/b_test.json",
        category_set=["bottle"],
        metrics=[
            EvaluationMetric(name="auroc", direction="maximize", implementation_ref="eval/metrics.py"),
            EvaluationMetric(name="latency", direction="minimize", implementation_ref="eval/metrics.py"),
        ],
        primary_metric="auroc",
        guardrails=["latency"],
        aggregation="mean",
        seeds=[1, 2, 3],
        checkpoint_selection="best_validation_primary_metric",
        resource_budget=EvaluationResourceBudget(max_wall_seconds=3600, max_gpu_seconds=1800),
        protected_paths=["eval/metrics.py", "splits/b_test.json"],
    )


def test_contract_freeze_is_content_stable_and_revisioned(tmp_path: Path):
    store = EvaluationContractStore()
    first = store.freeze(tmp_path, contract=_contract())
    replay = store.freeze(tmp_path, contract=_contract())
    assert replay == first
    assert store.current(tmp_path, session_id="session_fixture") == first

    with pytest.raises(ValueError, match="advance exactly once"):
        store.freeze(tmp_path, contract=_contract(revision=2, contract_id="evaluation_contract_000002"))

    second = store.freeze(tmp_path, contract=_contract(revision=1, contract_id="evaluation_contract_000002"))
    assert second.contract.revision == 1
    assert store.current(tmp_path, session_id="session_fixture") == second


def test_contract_rejects_btest_invariants_and_ambiguous_metrics():
    with pytest.raises(ValueError, match="primary_metric cannot"):
        EvaluationContract.model_validate(_contract().model_dump() | {"guardrails": ["auroc"]})
    with pytest.raises(ValueError, match="must be a non-empty relative path"):
        EvaluationMetric(name="auroc", direction="maximize", implementation_ref="../eval.py")


def test_freeze_protected_artifacts_requires_exact_existing_relative_paths(tmp_path: Path):
    target = tmp_path / "eval" / "metrics.py"
    target.parent.mkdir()
    target.write_text("metric implementation", encoding="utf-8")
    hashes = freeze_protected_artifacts(tmp_path, ["eval/metrics.py"])
    assert set(hashes) == {"eval/metrics.py"}
    with pytest.raises(FileNotFoundError, match="protected artifact"):
        freeze_protected_artifacts(tmp_path, ["eval/missing.py"])


def test_session_only_advances_to_the_frozen_contract_revision(tmp_path: Path):
    session, _ = ExperimentSessionStore().create_or_get(
        tmp_path,
        task_ref="task.json",
        task_hash="b" * 64,
        execution_mode="agent_assisted_after_approval",
    )
    contract = _contract()
    contract = contract.model_copy(update={"session_id": session.session_id})
    frozen = EvaluationContractStore().freeze(tmp_path, contract=contract)
    bound = ExperimentSessionStore().bind_evaluation_contract(
        tmp_path,
        session_id=session.session_id,
        evaluation_contract_ref=frozen.ref,
        evaluation_contract_sha256=frozen.sha256,
        evaluation_contract_revision=frozen.contract.revision,
    )
    assert bound.evaluation_contract_sha256 == frozen.sha256
    with pytest.raises(ValueError, match="advance exactly once"):
        ExperimentSessionStore().bind_evaluation_contract(
            tmp_path,
            session_id=session.session_id,
            evaluation_contract_ref=frozen.ref,
            evaluation_contract_sha256=frozen.sha256,
            evaluation_contract_revision=2,
        )
