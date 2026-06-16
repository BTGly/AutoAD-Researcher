"""Internal benchmark runtime evidence models.

All models use extra="forbid" and str_strip_whitespace=True.
SHA values must be 64-char lowercase hex. Commits must be 40-char lowercase hex.
Paths must be safe relative paths. Execution success requires full fingerprint evidence.
"""

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


# ------------------------------------------------------------------
# Shared constraints
# ------------------------------------------------------------------

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitCommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
SafeRelativePath = Annotated[
    str,
    StringConstraints(pattern=r"^(?!\.\./)(?!\.\.)(?!\/)(?!\.$)[^\0]+$"),
]

_SAFE_RELATIVE_RE = re.compile(r"^(?!\.\.\/)(?!\.\.)(?!\/)(?!\.$)[^\0]+$")

_ALLOWED_COMMAND_ENV_KEYS = {
    "PYTHONPATH", "TORCH_HOME", "PYTHONHASHSEED",
    "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX",
    "MPLCONFIGDIR", "WANDB_MODE", "HF_HUB_OFFLINE", "CUDA_VISIBLE_DEVICES",
}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _hex_len(v: str, n: int) -> str:
    if not re.fullmatch(rf"^[0-9a-f]{{{n}}}$", v):
        raise ValueError(f"must be {n}-char lowercase hex")
    return v

def _safe_path(v: str) -> str:
    if not _SAFE_RELATIVE_RE.match(v):
        raise ValueError(f"unsafe path: {v!r}")
    return v


# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------

BenchmarkAttemptStatus = Literal[
    "preflight_failed",
    "execution_failed",
    "metric_parse_failed",
    "invalid_repository_mutation",
    "success",
]

AllowedAttempt = Literal["attempt_01", "attempt_02"]
BenchmarkMetricsStatus = Literal["success", "metric_parse_failed"]
MetricUnit = Literal["ratio", "percent", "seconds", "bytes", "count"]
Accelerator = Literal["cpu", "cuda"]
ManifestStrategy = Literal["relative_path_size_v1"]
PreflightCheckStatus = Literal["passed", "failed", "skipped"]


# ------------------------------------------------------------------
# File fingerprint
# ------------------------------------------------------------------


class BenchmarkFileFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: Sha256Hex


# ------------------------------------------------------------------
# Repository state
# ------------------------------------------------------------------


class BenchmarkRepositoryState(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    case_id: str = Field(min_length=1)
    expected_commit: GitCommitSha
    actual_commit: GitCommitSha
    detached_head: bool
    dirty: bool
    required_files: list[BenchmarkFileFingerprint] = Field(default_factory=list)
    repository_fingerprint: Sha256Hex


# ------------------------------------------------------------------
# Environment snapshot
# ------------------------------------------------------------------


class BenchmarkEnvironmentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    python_version: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    accelerator: Accelerator
    torch_version: str = Field(min_length=1)
    torchvision_version: str = Field(min_length=1)
    cuda_available: bool
    cuda_device_count: int = Field(ge=0)
    cuda_runtime: str | None = None
    nvidia_driver: str | None = None
    gpu_name: str | None = None
    gpu_memory_mb: int | None = Field(default=None, gt=0)
    faiss_version: str | None = None
    timm_version: str | None = None
    lockfile_sha256: Sha256Hex
    environment_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_cuda_consistency(self):
        if self.accelerator == "cuda" and not self.cuda_available:
            raise ValueError("cuda accelerator requires cuda_available=true")
        if not self.cuda_available and self.cuda_device_count != 0:
            raise ValueError("cuda unavailable requires device_count=0")
        return self


# ------------------------------------------------------------------
# Weight manifest
# ------------------------------------------------------------------


class BenchmarkWeightEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    relative_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)
    sha256: Sha256Hex


class BenchmarkWeightManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    backbone: str = Field(min_length=1)
    framework: str = Field(min_length=1)
    torchvision_version: str = Field(min_length=1)
    cache_root_label: Literal["TORCH_HOME"] = "TORCH_HOME"
    files: list[BenchmarkWeightEntry] = Field(default_factory=list)
    offline_load_verified: bool
    weight_manifest_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_offline_requires_files(self):
        if self.offline_load_verified and not self.files:
            raise ValueError("offline_load_verified requires at least one file")
        return self


# ------------------------------------------------------------------
# Dataset manifest
# ------------------------------------------------------------------


class BenchmarkDatasetFileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    relative_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)


class BenchmarkDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    dataset_name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    root_env: str = Field(min_length=1)
    manifest_strategy: ManifestStrategy = "relative_path_size_v1"
    files: list[BenchmarkDatasetFileEntry] = Field(min_length=1)
    train_good_count: int = Field(ge=0)
    test_good_count: int = Field(ge=0)
    test_anomaly_count: int = Field(ge=0)
    mask_count: int = Field(ge=0)
    manifest_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_files_sorted_and_unique(self):
        paths = [f.relative_path for f in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate relative_path in dataset manifest")
        if paths != sorted(paths):
            raise ValueError("dataset manifest files must be sorted by relative_path")
        return self


# ------------------------------------------------------------------
# Command spec
# ------------------------------------------------------------------


class BenchmarkCommandSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    shell: Literal[False]
    argv_template: list[str] = Field(min_length=1)
    cwd: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(ge=1)
    network_guard: str = Field(min_length=1)
    resolved_argv_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_env_keys(self):
        for key in self.environment:
            if key not in _ALLOWED_COMMAND_ENV_KEYS:
                raise ValueError(f"forbidden environment key: {key!r}")
        return self


# ------------------------------------------------------------------
# Metric value
# ------------------------------------------------------------------


class BenchmarkMetricValue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: float = Field(allow_inf_nan=False)
    unit: MetricUnit
    required: bool


class BenchmarkMetricsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    status: BenchmarkMetricsStatus
    source: str = Field(min_length=1)
    source_sha256: Sha256Hex
    dataset_row: str | None = None
    metrics: dict[str, BenchmarkMetricValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_metrics_consistency(self):
        if self.status == "success":
            if not self.metrics:
                raise ValueError("success metrics must be non-empty")
            if not any(m.required for m in self.metrics.values()):
                raise ValueError("success metrics must include at least one required metric")
            if self.dataset_row is None:
                raise ValueError("success metrics requires dataset_row")
        elif self.status == "metric_parse_failed":
            if self.metrics:
                raise ValueError("parse failure must have empty metrics")
        return self

    @property
    def is_success(self) -> bool:
        return self.status == "success"


# ------------------------------------------------------------------
# Preflight
# ------------------------------------------------------------------


class BenchmarkPreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    status: PreflightCheckStatus
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class BenchmarkPreflightReport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    case_id: str = Field(min_length=1)
    attempt: AllowedAttempt
    checks: list[BenchmarkPreflightCheck] = Field(min_length=1)
    passed: bool

    @model_validator(mode="after")
    def _validate_passed_consistency(self):
        expected = all(c.status == "passed" for c in self.checks)
        if self.passed != expected:
            raise ValueError("passed must match all checks status=passed")
        return self


# ------------------------------------------------------------------
# Execution result
# ------------------------------------------------------------------


class BenchmarkExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    attempt: AllowedAttempt
    status: BenchmarkAttemptStatus
    exit_code: int | None = None
    timed_out: bool = False
    failure_code: str | None = None
    failure_message: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0.0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    repository_fingerprint_before: Sha256Hex | None = None
    repository_fingerprint_after: Sha256Hex | None = None
    case_sha256: Sha256Hex | None = None
    environment_sha256: Sha256Hex | None = None
    dataset_manifest_sha256: Sha256Hex | None = None
    weights_manifest_sha256: Sha256Hex | None = None
    evaluation_contract_sha256: Sha256Hex | None = None
    command_sha256: Sha256Hex | None = None
    metrics_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def _validate_status_consistency(self):
        if self.status == "success":
            if self.exit_code != 0:
                raise ValueError("success requires exit_code=0")
            if self.timed_out:
                raise ValueError("success requires timed_out=false")
            if self.started_at is None or self.finished_at is None:
                raise ValueError("success requires started_at and finished_at")
            if self.finished_at < self.started_at:
                raise ValueError("finished_at must be >= started_at")
            required_shas = [
                "repository_fingerprint_before", "repository_fingerprint_after",
                "case_sha256", "environment_sha256", "dataset_manifest_sha256",
                "weights_manifest_sha256", "evaluation_contract_sha256",
                "command_sha256", "metrics_sha256",
            ]
            for name in required_shas:
                if getattr(self, name) is None:
                    raise ValueError(f"success requires {name}")
        elif self.status == "preflight_failed":
            if self.exit_code is not None:
                raise ValueError("preflight_failed must not set exit_code")
        elif self.status == "execution_failed":
            if self.exit_code is None and not self.timed_out:
                raise ValueError("execution_failed requires exit_code or timed_out")
        elif self.status == "invalid_repository_mutation":
            if self.repository_fingerprint_before is None or self.repository_fingerprint_after is None:
                raise ValueError("repository_mutation requires both fingerprints")
        return self
