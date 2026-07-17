from pathlib import Path

from autoad_researcher.experiment.failure_classifier import classify_or_load


def test_classifier_uses_health_event_before_stderr_fallback(tmp_path: Path):
    (tmp_path / "health_events.jsonl").write_text('{"event":"OOM_DETECTED"}\n', encoding="utf-8")
    (tmp_path / "stderr.log").write_text("CUDA out of memory", encoding="utf-8")
    verdict = classify_or_load(tmp_path)
    assert verdict.matched_detector == "oom_error"
    assert verdict.failure_code == "OOM"
    assert classify_or_load(tmp_path) == verdict
