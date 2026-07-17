import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from autoad_researcher.benchmarks.hashing import sha256_file
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
    assert not (tmp_path / ".outcome_card.lock").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_protected_hash_validation_uses_observed_postrun_value(tmp_path: Path):
    run_dir = tmp_path / "run"
    attempt_dir = run_dir / "attempts" / "attempt_000005"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "execution_result.json").write_text("{}", encoding="utf-8")
    (attempt_dir / "metrics.json").write_text(json.dumps({"score": 1}), encoding="utf-8")
    protected = run_dir / "eval.py"
    protected.write_text("original evaluator", encoding="utf-8")
    contract = run_dir / "contract.json"
    contract.write_text(json.dumps({"metrics":[{"name":"score","required":True,"direction":"maximize","unit":"ratio","absolute_tolerance":0}],"evaluator_paths":["eval.py"],"protected_paths":["eval.py"],"raw_result_paths":["metrics.json"],"fingerprint_strategy":"repo_commit_paths_and_config_v1"}), encoding="utf-8")
    baseline = run_dir / "protected_hashes.json"
    baseline.write_text(json.dumps({"schema_version": 1, "hashes": {"eval.py": sha256_file(protected)}}), encoding="utf-8")

    kwargs = {
        "run_dir": run_dir,
        "evaluation_contract_ref": "contract.json",
        "evaluation_contract_sha256": sha256_file(contract),
        "protected_artifact_report_ref": "protected_hashes.json",
        "protected_artifact_report_sha256": sha256_file(baseline),
    }
    first = finalize_attempt(attempt_dir, attempt_id="attempt_000005", runtime_status="COMPLETED", **kwargs)
    assert first.attempt_category == "scientifically_evaluable"
    report = json.loads((attempt_dir / "protected_artifact_validation.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"

    protected.write_text("changed evaluator", encoding="utf-8")
    second_dir = run_dir / "attempts" / "attempt_000006"
    second_dir.mkdir()
    (second_dir / "execution_result.json").write_text("{}", encoding="utf-8")
    (second_dir / "metrics.json").write_text(json.dumps({"score": 1}), encoding="utf-8")
    second = finalize_attempt(second_dir, attempt_id="attempt_000006", runtime_status="COMPLETED", **kwargs)
    assert second.attempt_category == "protocol_violated"
    changed_report = json.loads((second_dir / "protected_artifact_validation.json").read_text(encoding="utf-8"))
    assert changed_report["changed_paths"] == ["eval.py"]


def test_finalizer_recovers_a_stale_lock_owned_by_a_dead_process(tmp_path: Path):
    (tmp_path / "execution_result.json").write_text("{}", encoding="utf-8")
    (tmp_path / "metrics.json").write_text(json.dumps({"score": 1}), encoding="utf-8")
    lock = tmp_path / ".outcome_card.lock"
    lock.write_text(json.dumps({"pid": 999_999_999, "owner_token": "dead", "created_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    os.utime(lock, (time.time() - 60, time.time() - 60))
    card = finalize_attempt(tmp_path, attempt_id="attempt_000007", runtime_status="COMPLETED")
    assert card.attempt_category == "scientifically_evaluable"
    assert not lock.exists()
