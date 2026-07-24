"""Attempt resource telemetry tests."""

from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.experiment.resource_telemetry import GpuTelemetryCollector


def test_gpu_telemetry_persists_samples_and_measured_report(tmp_path: Path, monkeypatch):
    gpu_values = iter([[100.0], [200.0]])
    util_values = iter([[10.0], [40.0]])
    cpu_values = iter([0.1, 0.4])
    memory_values = iter([32.0, 48.0])
    monkeypatch.setattr(
        "autoad_researcher.experiment.resource_telemetry._query_gpu",
        lambda device_ids, field: next(gpu_values if field == "memory.used" else util_values),
    )
    monkeypatch.setattr(
        "autoad_researcher.experiment.resource_telemetry._process_cpu_time_seconds",
        lambda pid: next(cpu_values),
    )
    monkeypatch.setattr(
        "autoad_researcher.experiment.resource_telemetry._process_memory_mb",
        lambda pid: next(memory_values),
    )

    collector = GpuTelemetryCollector(
        tmp_path,
        attempt_id="attempt_000001",
        attempt_purpose="confirmation",
        device_ids=["1"],
        started_at="2026-07-24T00:00:00+00:00",
    )
    collector.sample(123)
    collector.sample(123)
    report_path = collector.finish(runtime_status="COMPLETED")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["measurement_kind"] == "measured"
    assert report["gpu_count_used"] == 1
    assert report["peak_gpu_memory_mb"] == 200.0
    assert report["avg_gpu_memory_mb"] == 150.0
    assert report["peak_gpu_utilization_pct"] == 40.0
    assert report["avg_gpu_utilization_pct"] == 25.0
    assert report["cpu_time_seconds"] == 0.4
    assert report["peak_cpu_memory_mb"] == 48.0
    assert report["subject_type"] == "variant"
    assert report["variant_id"] == "attempt_000001"
    assert len((tmp_path / "resource_samples.jsonl").read_text(encoding="utf-8").splitlines()) == 2
