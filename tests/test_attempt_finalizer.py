import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from autoad_researcher.experiment.finalizer import finalize_attempt

def test_finalizer_writes_one_immutable_outcome_card(tmp_path: Path):
    (tmp_path / "execution_result.json").write_text("{}", encoding="utf-8")
    (tmp_path / "metrics.json").write_text(json.dumps({"auroc": .9}), encoding="utf-8")
    card = finalize_attempt(tmp_path, attempt_id="attempt_000001", runtime_status="COMPLETED")
    assert card.attempt_category == "scientifically_evaluable"
    assert card.metrics == {"auroc": .9}
    assert finalize_attempt(tmp_path, attempt_id="attempt_000001", runtime_status="FAILED") == card

def test_completed_without_valid_metrics_is_not_scientifically_evaluable(tmp_path: Path):
    (tmp_path / "execution_result.json").write_text("{}", encoding="utf-8")
    card = finalize_attempt(tmp_path, attempt_id="attempt_000002", runtime_status="COMPLETED")
    assert card.attempt_category == "run_failed"

def test_contract_hash_mismatch_is_protocol_violation(tmp_path: Path):
    attempt_dir = tmp_path / "attempt"; attempt_dir.mkdir()
    (attempt_dir / "execution_result.json").write_text("{}", encoding="utf-8")
    (attempt_dir / "metrics.json").write_text(json.dumps({"score": 1}), encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"metrics":[{"name":"score","required":True,"direction":"maximize","unit":"ratio","absolute_tolerance":0}],"evaluator_paths":["eval.py"],"protected_paths":["eval.py"],"raw_result_paths":["metrics.json"],"fingerprint_strategy":"repo_commit_paths_and_config_v1"}), encoding="utf-8")
    card = finalize_attempt(attempt_dir, attempt_id="attempt_000003", runtime_status="COMPLETED", run_dir=tmp_path, evaluation_contract_ref="contract.json", evaluation_contract_sha256="0" * 64)
    assert card.attempt_category == "protocol_violated"

def test_concurrent_finalizers_return_one_complete_card(tmp_path: Path):
    (tmp_path / "execution_result.json").write_text("{}", encoding="utf-8")
    (tmp_path / "metrics.json").write_text(json.dumps({"score": 1}), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=2) as executor:
        cards = list(executor.map(lambda _: finalize_attempt(tmp_path, attempt_id="attempt_000004", runtime_status="COMPLETED"), range(2)))
    assert cards[0] == cards[1]
    assert json.loads((tmp_path / "outcome_card.json").read_text(encoding="utf-8"))["attempt_id"] == "attempt_000004"
