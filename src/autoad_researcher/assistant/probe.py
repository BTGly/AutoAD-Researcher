"""silent_probe / WhatWeKnow for AutoAD Assistant intent alignment.

The probe reads only a hard-coded set of run artifacts under a validated run_id.
It does not call LLMs, does not run commands, and does not accept user-provided
path components beyond the validated run_id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.core.run_id import run_dir_path


KNOWN_ARTIFACT_MAP: dict[str, Path] = {
    "baseline_contract": Path("baseline_architecture_contract.json"),
    "repo_summary": Path("repository_intelligence/repo_summary.json"),
    "paper_sources": Path("paper/artifacts/paper_idea_sources.json"),
    "paper_summary": Path("paper/artifacts/paper_summary.json"),
    "context_draft": Path("context/research_context_draft.json"),
    "variants": Path("transfer_design/implementation_variants.json"),
    "transfer_analysis": Path("transfer_design/transfer_analysis.json"),
}


class WhatWeKnow(BaseModel):
    """Probe-derived summary of existing run artifacts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str

    has_baseline_contract: bool = False
    has_repo_summary: bool = False
    has_paper_artifacts: bool = False
    has_context_draft: bool = False
    has_implementation_variants: bool = False
    has_transfer_analysis: bool = False

    baseline_method: str | None = None
    baseline_commit: str | None = None
    modifiable_hooks: list[str] = Field(default_factory=list)

    dataset: str | None = None
    primary_metric: str | None = None

    paper_methods: list[str] = Field(default_factory=list)
    available_variants: list[str] = Field(default_factory=list)
    unresolved_dimensions: list[str] = Field(default_factory=list)

    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_artifacts: list[str] = Field(default_factory=list)


def silent_probe(run_id: str, *, runs_root: str | Path = "runs") -> WhatWeKnow:
    """Read known assistant context artifacts for a run without side effects."""
    run_dir = run_dir_path(runs_root, run_id)
    artifact_data: dict[str, Any] = {}
    evidence_artifacts: list[str] = []
    warnings: list[str] = []

    for artifact_key, relative_path in KNOWN_ARTIFACT_MAP.items():
        path = run_dir / relative_path
        if not path.is_file():
            continue
        try:
            artifact_data[artifact_key] = json.loads(path.read_text(encoding="utf-8"))
            evidence_artifacts.append(relative_path.as_posix())
        except Exception as exc:  # malformed user/run artifact should not crash the assistant
            warnings.append(f"invalid_json:{relative_path.as_posix()}:{type(exc).__name__}")

    baseline = _as_dict(artifact_data.get("baseline_contract"))
    context = _as_dict(artifact_data.get("context_draft"))
    transfer_analysis = _as_dict(artifact_data.get("transfer_analysis"))

    paper_methods = _extract_paper_methods(
        artifact_data.get("paper_sources"),
        artifact_data.get("paper_summary"),
    )
    available_variants = _dedupe(
        [*_extract_variant_ids(artifact_data.get("variants")), *_extract_transfer_variant_ids(transfer_analysis)]
    )

    result = WhatWeKnow(
        run_id=run_id,
        has_baseline_contract="baseline_contract" in artifact_data,
        has_repo_summary="repo_summary" in artifact_data,
        has_paper_artifacts="paper_sources" in artifact_data or "paper_summary" in artifact_data,
        has_context_draft="context_draft" in artifact_data,
        has_implementation_variants="variants" in artifact_data,
        has_transfer_analysis="transfer_analysis" in artifact_data,
        baseline_method=_clean_str(baseline.get("model_name")),
        baseline_commit=_clean_str(baseline.get("repository_commit")),
        modifiable_hooks=_extract_hook_names(baseline.get("modifiable_hooks")),
        dataset=_extract_dataset(context.get("dataset")),
        primary_metric=_extract_primary_metric(context.get("metrics")),
        paper_methods=paper_methods,
        available_variants=available_variants,
        unresolved_dimensions=_extract_unresolved_dimensions(transfer_analysis.get("unresolved_dimensions")),
        warnings=warnings,
        evidence_artifacts=evidence_artifacts,
    )
    return result.model_copy(update={"missing_fields": _missing_fields(result)})


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _extract_hook_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    hooks: list[str] = []
    for item in value:
        if isinstance(item, dict):
            hook = _clean_str(item.get("hook_name"))
            if hook is not None:
                hooks.append(hook)
    return _dedupe(hooks)


def _extract_dataset(value: Any) -> str | None:
    if isinstance(value, str):
        return _clean_str(value)
    if isinstance(value, dict):
        return _clean_str(value.get("dataset_name")) or _clean_str(value.get("name"))
    return None


def _extract_primary_metric(value: Any) -> str | None:
    if isinstance(value, str):
        return _clean_str(value)
    if isinstance(value, list):
        for item in value:
            metric = _clean_str(item)
            if metric is not None:
                return metric
    if isinstance(value, dict):
        metrics = value.get("primary_metrics")
        if isinstance(metrics, list):
            for item in metrics:
                metric = _clean_str(item)
                if metric is not None:
                    return metric
        return _clean_str(value.get("primary_metric"))
    return None


def _extract_paper_methods(paper_sources: Any, paper_summary: Any) -> list[str]:
    methods: list[str] = []
    for item in _iter_dicts(paper_sources):
        for key in ("label", "mechanism_summary", "mechanism_why"):
            value = _clean_str(item.get(key))
            if value is not None:
                methods.append(value)
    summary = _as_dict(paper_summary)
    for claim_group in (summary.get("proposed_method"), summary.get("core_components")):
        for claim in _iter_dicts(claim_group):
            value = _clean_str(claim.get("text")) or _clean_str(claim.get("value"))
            if value is not None:
                methods.append(value)
    return _dedupe(methods)


def _extract_variant_ids(value: Any) -> list[str]:
    return [item["variant_id"].strip() for item in _iter_dicts(value) if _clean_str(item.get("variant_id"))]


def _extract_transfer_variant_ids(transfer_analysis: dict[str, Any]) -> list[str]:
    variants: list[str] = []
    for key in ("viable_variant_ids", "conditional_variant_ids"):
        value = transfer_analysis.get(key)
        if isinstance(value, list):
            variants.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
    return variants


def _extract_unresolved_dimensions(value: Any) -> list[str]:
    dimensions: list[str] = []
    for item in _iter_dicts(value):
        dimension = _clean_str(item.get("dimension"))
        if dimension is not None:
            dimensions.append(dimension)
    return _dedupe(dimensions)


def _iter_dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _missing_fields(result: WhatWeKnow) -> list[str]:
    missing: list[str] = []
    if result.baseline_method is None:
        missing.append("baseline_method")
    if result.dataset is None:
        missing.append("dataset")
    if result.primary_metric is None:
        missing.append("primary_metric")
    # These are intentionally not inferred from current stable schemas.
    missing.append("category")
    missing.append("metric_direction")
    return _dedupe(missing)
