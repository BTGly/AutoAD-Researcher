"""Deterministic post-run failure classification with a durable sidecar cache."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class DetectorProfile(str, Enum):
    GPU_TRAINING = "gpu_training"
    CODING_AGENT = "coding_agent"
    CUSTOM = "custom"


class FailureClassifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: DetectorProfile = DetectorProfile.GPU_TRAINING
    enabled_detectors: list[str] | None = None
    disabled_detectors: list[str] = []


class FailureClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classifier_version: str = "autoad-gpu-v1"
    profile: DetectorProfile
    enabled_detectors: list[str]
    matched_detector: str
    failure_code: str
    attempt_category: str
    retryable: bool


_DEFAULT = ["execution_failure", "oom_error", "cuda_runtime_error", "cudnn_error", "disk_full", "nan_or_inf", "python_import_error", "metrics_missing", "unknown_run_failure"]
_RETRYABLE_EXECUTION_FAILURES = {
    "WORKER_LOST",
    "TEMPORARY_GPU_UNAVAILABLE",
    "TRANSIENT_IO_ERROR",
    "PROCESS_SPAWN_FAILED",
}


def classify_or_load(attempt_dir: Path, config: FailureClassifierConfig | None = None) -> FailureClassification:
    path = attempt_dir / "failure_classification.json"
    if path.is_file(): return FailureClassification.model_validate_json(path.read_text(encoding="utf-8"))
    config = config or FailureClassifierConfig()
    enabled = config.enabled_detectors or _DEFAULT
    enabled = [name for name in enabled if name not in config.disabled_detectors]
    execution = _read_json(attempt_dir / "execution_result.json")
    raw_failure_code = execution.get("failure_code") if isinstance(execution.get("failure_code"), str) else None
    if "execution_failure" in enabled and raw_failure_code in _RETRYABLE_EXECUTION_FAILURES:
        return _write(path, FailureClassification(profile=config.profile, enabled_detectors=enabled, matched_detector="execution_failure", failure_code=raw_failure_code, attempt_category="run_failed", retryable=True))
    stderr = _read(attempt_dir / "stderr.log").lower()
    events = _read(attempt_dir / "health_events.jsonl").lower()
    result = _read(attempt_dir / "execution_result.json").lower()
    for name, code, retryable, predicate in [
        ("oom_error", "OOM", True, "oom_detected" in events or "cuda out of memory" in stderr),
        ("cuda_runtime_error", "CUDA_RUNTIME_ERROR", True, "cuda runtime error" in stderr),
        ("cudnn_error", "CUDNN_ERROR", True, "cudnn" in stderr),
        ("disk_full", "DISK_FULL", False, "no space left on device" in stderr),
        ("nan_or_inf", "NAN_OR_INF", False, "nan_or_inf" in events or "nan" in stderr),
        ("python_import_error", "IMPORT_OR_SYNTAX_ERROR", False, "modulenotfounderror" in stderr or "syntaxerror" in stderr),
        ("metrics_missing", "METRICS_MISSING", False, "run_expected_output_missing" in result),
    ]:
        if name in enabled and predicate:
            return _write(path, FailureClassification(profile=config.profile, enabled_detectors=enabled, matched_detector=name, failure_code=code, attempt_category="run_failed", retryable=retryable))
    return _write(path, FailureClassification(profile=config.profile, enabled_detectors=enabled, matched_detector="unknown_run_failure", failure_code="UNKNOWN_RUN_FAILURE", attempt_category="run_failed", retryable=False))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}
def _write(path: Path, value: FailureClassification) -> FailureClassification:
    path.write_text(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return value
