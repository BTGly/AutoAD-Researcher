"""PR-004D deterministic runtime watchdog tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoad_researcher.experiment.watchdog import RuntimeWatchdog


def test_watchdog_emits_oom_nan_and_stale_heartbeat_once(tmp_path: Path):
    (tmp_path / "stdout.log").write_text("still running\n", encoding="utf-8")
    (tmp_path / "stderr.log").write_text("CUDA out of memory; loss=nan\n", encoding="utf-8")
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    (tmp_path / "heartbeat.json").write_text(json.dumps({"timestamp": (now - timedelta(seconds=40)).isoformat()}), encoding="utf-8")
    watchdog = RuntimeWatchdog(heartbeat_interval_seconds=15, stdout_stall_seconds=3600)

    first = watchdog.inspect(tmp_path, pid=None, now=now)
    second = watchdog.inspect(tmp_path, pid=None, now=now)

    assert {event.event for event in first} == {"OOM_DETECTED", "NAN_OR_INF", "STALE_HEARTBEAT"}
    assert second == []
    assert len((tmp_path / "health_events.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_watchdog_nonfinite_detection_ignores_info_and_inference(tmp_path: Path):
    (tmp_path / "stderr.log").write_text("INFO inference started\n", encoding="utf-8")
    assert "NAN_OR_INF" not in {event.event for event in RuntimeWatchdog().inspect(tmp_path, pid=None)}
    (tmp_path / "stderr.log").write_text("loss=nan; tensor=-inf\n", encoding="utf-8")
    assert "NAN_OR_INF" in {event.event for event in RuntimeWatchdog().inspect(tmp_path, pid=None)}
