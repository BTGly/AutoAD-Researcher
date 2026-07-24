"""Durable, best-effort resource telemetry for one GPU Attempt."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.schemas.execution import ResourceUsageReport

RESOURCE_REPORT_FILE = "resource_usage_report.json"
RESOURCE_SAMPLES_FILE = "resource_samples.jsonl"


class GpuTelemetryCollector:
    """Sample assigned GPUs and the child process without adding a runtime dependency."""

    def __init__(
        self,
        output_dir: Path,
        *,
        attempt_id: str,
        attempt_purpose: str,
        device_ids: list[str],
        started_at: str | None = None,
    ):
        self.output_dir = output_dir
        self.attempt_id = attempt_id
        self.attempt_purpose = attempt_purpose
        self.device_ids = list(device_ids)
        self.started_at = _parse_time(started_at) if started_at else datetime.now(timezone.utc)
        self._samples = _load_samples(output_dir / RESOURCE_SAMPLES_FILE)

    def sample(self, pid: int | None) -> None:
        sample = {
            "timestamp": _utc_now(),
            "gpu_memory_mb": _gpu_memory_mb(self.device_ids),
            "gpu_utilization_pct": _gpu_utilization_pct(self.device_ids),
            "cpu_time_seconds": _process_cpu_time_seconds(pid),
            "cpu_memory_mb": _process_memory_mb(pid),
        }
        self._samples.append(sample)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with (self.output_dir / RESOURCE_SAMPLES_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def finish(self, *, runtime_status: str) -> Path:
        finished_at = datetime.now(timezone.utc)
        gpu_memory = _values(self._samples, "gpu_memory_mb")
        gpu_utilization = _values(self._samples, "gpu_utilization_pct")
        cpu_times = _values(self._samples, "cpu_time_seconds")
        cpu_memory = _values(self._samples, "cpu_memory_mb")
        fields: dict[str, float | int | None] = {
            "gpu_count_used": len(self.device_ids),
            "peak_gpu_memory_mb": max(gpu_memory) if gpu_memory else None,
            "avg_gpu_memory_mb": sum(gpu_memory) / len(gpu_memory) if gpu_memory else None,
            "peak_gpu_utilization_pct": max(gpu_utilization) if gpu_utilization else None,
            "avg_gpu_utilization_pct": sum(gpu_utilization) / len(gpu_utilization) if gpu_utilization else None,
            "wall_time_seconds": max(0.0, (finished_at - self.started_at).total_seconds()),
            "cpu_time_seconds": max(cpu_times) if cpu_times else None,
            "peak_cpu_memory_mb": max(cpu_memory) if cpu_memory else None,
        }
        measured_fields = [
            fields["gpu_count_used"],
            fields["peak_gpu_memory_mb"],
            fields["avg_gpu_memory_mb"],
            fields["peak_gpu_utilization_pct"],
            fields["avg_gpu_utilization_pct"],
            fields["wall_time_seconds"],
            fields["cpu_time_seconds"],
            fields["peak_cpu_memory_mb"],
        ]
        measurement_kind = "measured" if all(value is not None for value in measured_fields) else "partially_measured"
        report = ResourceUsageReport(
            attempt_id=self.attempt_id,
            unit_id=self.attempt_id,
            subject_type="baseline" if self.attempt_purpose in {"baseline", "repair", "noise_calibration"} else "variant",
            variant_id=None if self.attempt_purpose in {"baseline", "repair", "noise_calibration"} else self.attempt_id,
            measurement_kind=measurement_kind,
            measurement_tool="nvidia-smi+procfs",
            **fields,
        )
        path = self.output_dir / RESOURCE_REPORT_FILE
        _write_json_atomic(path, report.model_dump(mode="json"))
        return path


def _gpu_memory_mb(device_ids: list[str]) -> float | None:
    values = _query_gpu(device_ids, "memory.used")
    return sum(values) if values else None


def _gpu_utilization_pct(device_ids: list[str]) -> float | None:
    values = _query_gpu(device_ids, "utilization.gpu")
    return sum(values) / len(values) if values else None


def _query_gpu(device_ids: list[str], field: str) -> list[float]:
    if not device_ids:
        return []
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu=index,{field}",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    selected = set(device_ids)
    values: list[float] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2 or parts[0] not in selected:
            continue
        try:
            values.append(float(parts[1]))
        except ValueError:
            continue
    return values


def _process_cpu_time_seconds(pid: int | None) -> float | None:
    values = _read_proc_stat(pid)
    if values is None:
        return None
    utime, stime = values
    try:
        return (utime + stime) / os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        return None


def _process_memory_mb(pid: int | None) -> float | None:
    if pid is None:
        return None
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return float(line.split()[1]) / 1024.0
    except (FileNotFoundError, IndexError, ValueError, OSError):
        return None
    return None


def _read_proc_stat(pid: int | None) -> tuple[int, int] | None:
    if pid is None:
        return None
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return int(fields[13]), int(fields[14])
    except (FileNotFoundError, IndexError, ValueError, OSError):
        return None


def _load_samples(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    samples: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            samples.append(value)
    return samples


def _values(samples: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for sample in samples:
        value = sample.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("telemetry start time must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
