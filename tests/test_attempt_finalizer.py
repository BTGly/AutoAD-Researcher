import json
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
