from pathlib import Path

from autoad_researcher.experiment.failure_classifier import classify_or_load


def test_classifier_uses_health_event_before_stderr_fallback(tmp_path: Path):
    (tmp_path / "health_events.jsonl").write_text('{"event":"OOM_DETECTED"}\n', encoding="utf-8")
    (tmp_path / "stderr.log").write_text("CUDA out of memory", encoding="utf-8")
    verdict = classify_or_load(tmp_path)
    assert verdict.matched_detector == "oom_error"
    assert verdict.failure_code == "OOM"
    assert classify_or_load(tmp_path) == verdict


def test_classifier_prefers_structured_execution_failure_for_retry_policy(tmp_path: Path):
    (tmp_path / "execution_result.json").write_text(
        '{"failure_code": "PROCESS_SPAWN_FAILED"}', encoding="utf-8"
    )
    verdict = classify_or_load(tmp_path)
    assert verdict.matched_detector == "execution_failure"
    assert verdict.retryable is True


def test_classifier_keeps_structured_disk_full_non_retryable(tmp_path: Path):
    (tmp_path / "execution_result.json").write_text(
        '{"failure_code": "DISK_FULL"}', encoding="utf-8"
    )
    verdict = classify_or_load(tmp_path)
    assert verdict.failure_code == "DISK_FULL"
    assert verdict.retryable is False
