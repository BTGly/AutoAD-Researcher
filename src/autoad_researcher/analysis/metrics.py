"""Structured metrics parsing from raw experiment outputs."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file


class MetricParseSpec(BaseModel):
    """One metric to parse from a JSON source file."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    metric_name: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    json_path: list[str | int] = Field(min_length=1)
    dataset_row: str = Field(min_length=1)
    unit: Literal["ratio", "percent", "seconds", "bytes", "count"]
    required: bool


class ParsedMetric(BaseModel):
    """Parsed metric with source evidence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    metric_name: str
    source_path: str
    source_sha256: str | None = None
    dataset_row: str
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: str
    required: bool
    parse_status: Literal["parsed", "missing", "invalid"]
    failure_message: str | None = None


class MetricsReport(BaseModel):
    """Metrics parsed from raw sources."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    metrics: list[ParsedMetric]
    required_parsed: int
    required_total: int
    status: Literal["passed", "failed"]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def parse_metrics(attempt_dir: Path | str, specs: list[MetricParseSpec]) -> MetricsReport:
    """Parse metrics from structured JSON files under an attempt directory."""
    root = Path(attempt_dir)
    metrics = [_parse_one(root, spec) for spec in specs]
    required = [metric for metric in metrics if metric.required]
    required_parsed = sum(1 for metric in required if metric.parse_status == "parsed")
    payload = {
        "schema_version": 1,
        "metrics": [metric.model_dump(mode="json", exclude_none=True) for metric in metrics],
        "required_parsed": required_parsed,
        "required_total": len(required),
        "status": "passed" if required_parsed == len(required) else "failed",
    }
    payload["report_sha256"] = canonical_sha256(payload)
    return MetricsReport.model_validate(payload)


def _parse_one(root: Path, spec: MetricParseSpec) -> ParsedMetric:
    try:
        source = _resolve_inside(root, spec.source_path)
    except ValueError as exc:
        return _failed(spec, "invalid", str(exc), source_sha256=None)

    if not source.is_file():
        return _failed(spec, "missing", "source file missing", source_sha256=None)
    source_sha = sha256_file(source)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        value = _get_json_path(data, spec.json_path)
        if not isinstance(value, int | float) or isinstance(value, bool):
            return _failed(spec, "invalid", "metric value must be numeric", source_sha256=source_sha)
        return ParsedMetric(
            metric_name=spec.metric_name,
            source_path=spec.source_path,
            source_sha256=source_sha,
            dataset_row=spec.dataset_row,
            value=float(value),
            unit=spec.unit,
            required=spec.required,
            parse_status="parsed",
        )
    except Exception as exc:
        return _failed(spec, "invalid", str(exc), source_sha256=source_sha)


def _failed(
    spec: MetricParseSpec,
    status: Literal["missing", "invalid"],
    message: str,
    *,
    source_sha256: str | None,
) -> ParsedMetric:
    return ParsedMetric(
        metric_name=spec.metric_name,
        source_path=spec.source_path,
        source_sha256=source_sha256,
        dataset_row=spec.dataset_row,
        unit=spec.unit,
        required=spec.required,
        parse_status=status,
        failure_message=message,
    )


def _get_json_path(data, path: list[str | int]):
    current = data
    for part in path:
        current = current[part]
    return current


def _resolve_inside(root: Path, relative: str) -> Path:
    candidate = root / relative
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        raise ValueError(f"path escapes attempt dir: {relative}") from None
    return candidate
