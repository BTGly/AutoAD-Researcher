"""Internal benchmark runtime evidence models.

All models use extra="forbid" and str_strip_whitespace=True.
Status enum restricts to known states.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


# ------------------------------------------------------------------
# File fingerprint
# ------------------------------------------------------------------


class BenchmarkFileFingerprint(BaseModel):
    """单个文件的指纹。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


# ------------------------------------------------------------------
# Repository state
# ------------------------------------------------------------------


class BenchmarkRepositoryState(BaseModel):
    """执行前/后的仓库状态。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    case_id: str = Field(min_length=1)
    expected_commit: str = Field(min_length=40, max_length=40)
    actual_commit: str = Field(min_length=40, max_length=40)
    detached_head: bool
    dirty: bool
    required_files: list[BenchmarkFileFingerprint] = Field(default_factory=list)
    repository_fingerprint: str = Field(min_length=64, max_length=64)


# ------------------------------------------------------------------
# Environment snapshot
# ------------------------------------------------------------------


class BenchmarkEnvironmentSnapshot(BaseModel):
    """Python 环境和依赖快照。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    python_version: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    accelerator: str = Field(min_length=1)
    torch_version: str = Field(min_length=1)
    torchvision_version: str = Field(min_length=1)
    cuda_available: bool
    cuda_device_count: int
    gpu_name: str | None = None
    gpu_memory_mb: int | None = None
    lockfile_sha256: str = Field(min_length=64, max_length=64)
    environment_sha256: str = Field(min_length=64, max_length=64)


# ------------------------------------------------------------------
# Weight manifest
# ------------------------------------------------------------------


class BenchmarkWeightEntry(BaseModel):
    """单个权重文件。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    relative_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class BenchmarkWeightManifest(BaseModel):
    """Backbone 权重清单。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    backbone: str = Field(min_length=1)
    framework: str = Field(min_length=1)
    torchvision_version: str = Field(min_length=1)
    files: list[BenchmarkWeightEntry] = Field(default_factory=list)
    offline_load_verified: bool
    weight_manifest_sha256: str = Field(min_length=64, max_length=64)


# ------------------------------------------------------------------
# Dataset manifest
# ------------------------------------------------------------------


class BenchmarkDatasetFileEntry(BaseModel):
    """单个数据集文件。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    relative_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)


class BenchmarkDatasetManifest(BaseModel):
    """数据集 structural manifest。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    dataset_name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    root_env: str = Field(min_length=1)
    train_good_count: int = Field(ge=0)
    test_good_count: int = Field(ge=0)
    test_anomaly_count: int = Field(ge=0)
    mask_count: int = Field(ge=0)
    manifest_sha256: str = Field(min_length=64, max_length=64)


# ------------------------------------------------------------------
# Command spec
# ------------------------------------------------------------------


class BenchmarkCommandSpec(BaseModel):
    """确定性执行命令。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    shell: Literal[False]
    argv_template: list[str] = Field(min_length=1)
    cwd: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(ge=1)
    network_guard: str = Field(min_length=1)
    resolved_argv_sha256: str = Field(min_length=64, max_length=64)


# ------------------------------------------------------------------
# Metric value
# ------------------------------------------------------------------


class BenchmarkMetricValue(BaseModel):
    """单个 metric 的值。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: float
    unit: str = Field(min_length=1)
    required: bool


class BenchmarkMetricsResult(BaseModel):
    """解析后的 metrics。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    status: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)
    dataset_row: str | None = None
    metrics: dict[str, BenchmarkMetricValue] = Field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == "success"


# ------------------------------------------------------------------
# Execution result
# ------------------------------------------------------------------


class BenchmarkExecutionResult(BaseModel):
    """单次 attempt 的完整结果。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    attempt: AllowedAttempt
    status: BenchmarkAttemptStatus
    exit_code: int | None = None
    timed_out: bool = False
    duration_seconds: float | None = Field(default=None, ge=0.0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    repository_fingerprint_before: str | None = None
    repository_fingerprint_after: str | None = None
    case_sha256: str | None = None
    environment_sha256: str | None = None
    dataset_manifest_sha256: str | None = None
    weights_manifest_sha256: str | None = None
    evaluation_contract_sha256: str | None = None
    command_sha256: str | None = None
    metrics_sha256: str | None = None
