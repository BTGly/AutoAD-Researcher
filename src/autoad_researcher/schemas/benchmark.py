"""Internal Benchmark schemas — 锁定的内部工程基准定义。

仅用于 Repository Reader、Runner、Metrics、Validity 和 CI 回归。
不得被 InputIntake、IntentClarifier、IdeaGenerator 或真实用户 CLI 读取。
"""

import re
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_FORBIDDEN_PLACEHOLDERS = re.compile(
    r"(TODO|TBD|PLACEHOLDER|REPLACE_ME|CHANGEME|FIXME|<[^>]+>)", re.IGNORECASE
)
_ALL_ZERO_SHA = "0" * 40


def _validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError(f"path must be relative: {value!r}")
    if ".." in path.parts:
        raise ValueError(f"path must not contain '..': {value!r}")
    if value in {"", "."}:
        raise ValueError(f"path must not be empty or '.'")
    return value


def _reject_placeholders(value: str, field_name: str) -> None:
    if _FORBIDDEN_PLACEHOLDERS.search(value):
        raise ValueError(f"placeholder found in {field_name}: {value!r}")


def _reject_placeholders_recursive(value: object, path: str) -> None:
    """Recursively check all strings for placeholders."""
    if isinstance(value, str):
        _reject_placeholders(value, path)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _reject_placeholders_recursive(item, f"{path}[{i}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_placeholders_recursive(item, f"{path}.{key}")


# ------------------------------------------------------------------
# BenchmarkRepository
# ------------------------------------------------------------------


class BenchmarkRepository(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str = Field(min_length=1)
    entrypoint_path: str = Field(min_length=1)
    config_path: str | None = None
    dependency_files: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_repo(self):
        if self.commit_sha == _ALL_ZERO_SHA:
            raise ValueError("commit_sha must not be all zeros")
        for attr in ("url", "ref", "license"):
            _reject_placeholders(getattr(self, attr), f"repository.{attr}")
        _validate_relative_path(self.entrypoint_path)
        if self.config_path:
            _validate_relative_path(self.config_path)
        for p in self.dependency_files:
            _validate_relative_path(p)
        return self


# ------------------------------------------------------------------
# BenchmarkDataset
# ------------------------------------------------------------------


class BenchmarkDatasetAcquisition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: Literal["user_provided"]
    source_page: str = Field(min_length=1)
    license: str = Field(min_length=1)
    redistribution_allowed: Literal[False]
    automatic_download: Literal[False]
    user_must_accept_license: Literal[True]

    @model_validator(mode="after")
    def _validate_acquisition(self):
        _reject_placeholders(self.source_page, "dataset.acquisition.source_page")
        _reject_placeholders(self.license, "dataset.acquisition.license")
        return self


class BenchmarkDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    root_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    license: str = Field(min_length=1)
    acquisition: BenchmarkDatasetAcquisition | None = None
    required_relative_paths: list[str] = Field(min_length=1)
    manifest_strategy: Literal["relative_path_size_v1"]

    @model_validator(mode="after")
    def _validate_dataset(self):
        _reject_placeholders(self.license, "dataset.license")
        for p in self.required_relative_paths:
            _validate_relative_path(p)
        return self


# ------------------------------------------------------------------
# BenchmarkMetric
# ------------------------------------------------------------------


class BenchmarkMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    required: bool
    direction: Literal["maximize", "minimize"]
    unit: Literal["ratio", "percent", "seconds", "bytes", "count"]
    absolute_tolerance: float = Field(ge=0, allow_inf_nan=False)


# ------------------------------------------------------------------
# BenchmarkEvaluationContract
# ------------------------------------------------------------------


class BenchmarkEvaluationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    metrics: list[BenchmarkMetric] = Field(min_length=1)
    evaluator_paths: list[str] = Field(min_length=1)
    protected_paths: list[str] = Field(min_length=1)
    raw_result_paths: list[str] = Field(min_length=1)
    fingerprint_strategy: Literal["repo_commit_paths_and_config_v1"]

    @model_validator(mode="after")
    def _validate_contract(self):
        names = [m.name.casefold() for m in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("duplicate metric name")
        if not any(m.required for m in self.metrics):
            raise ValueError("at least one required metric is needed")
        for p in self.evaluator_paths:
            _validate_relative_path(p)
        for p in self.protected_paths:
            _validate_relative_path(p)
        for p in self.raw_result_paths:
            _validate_relative_path(p)
        evaluator_set = set(self.evaluator_paths)
        protected_set = set(self.protected_paths)
        if not evaluator_set.issubset(protected_set):
            raise ValueError("evaluator_paths must be subset of protected_paths")
        return self


# ------------------------------------------------------------------
# BenchmarkReproducibility
# ------------------------------------------------------------------


class BenchmarkReproducibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempts: Literal[2]
    seed: int
    require_same_repository_commit: Literal[True]
    require_same_case_config: Literal[True]
    require_same_dataset_manifest: Literal[True]
    require_same_evaluation_contract: Literal[True]
    require_same_environment: bool = True


# ------------------------------------------------------------------
# BenchmarkSafety
# ------------------------------------------------------------------


class BenchmarkSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_network_during_execution: Literal[False]
    require_clean_repository: Literal[True]
    overwrite_existing_attempt: Literal[False]
    allow_paths_outside_workspace: Literal[False]


# ------------------------------------------------------------------
# InternalBenchmarkCase
# ------------------------------------------------------------------


class InternalBenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]

    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    scope: Literal["internal_benchmark_only"]
    must_not_be_used_as_user_default: Literal[True]

    purpose: str = Field(min_length=1)

    baseline_name: str = Field(min_length=1)
    implementation_name: str = Field(min_length=1)

    repository: BenchmarkRepository
    dataset: BenchmarkDataset

    fixed_parameters: dict[str, str | int | float | bool | list[str]]

    evaluation: BenchmarkEvaluationContract
    reproducibility: BenchmarkReproducibility
    safety: BenchmarkSafety

    @model_validator(mode="after")
    def _validate_no_placeholders_or_bad_floats(self):
        # Recursive placeholder check over all string fields
        _reject_placeholders_recursive(self.model_dump(mode="python"), "case")

        # NaN/Infinity check in fixed_parameters
        import math
        for key, val in self.fixed_parameters.items():
            if isinstance(val, float) and not math.isfinite(val):
                raise ValueError(f"fixed parameter {key!r} must be finite")
            if isinstance(val, list):
                for i, item in enumerate(val):
                    if isinstance(item, float) and not math.isfinite(item):
                        raise ValueError(f"fixed parameter {key!r}[{i}] must be finite")

        return self
