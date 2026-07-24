"""Deterministic validity, comparability, and effect classification."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.experiment.evaluation_contract import EvaluationContract


EvaluationStatus = Literal["COMPARABLE", "NON_COMPARABLE"]
ScientificEffect = Literal["IMPROVEMENT", "NO_EFFECT", "REGRESSION", "INCONCLUSIVE"]


class ImplementationEvidence(BaseModel):
    """Executor-produced facts; Finalizer never infers implementation semantics."""

    model_config = ConfigDict(extra="forbid")

    patch_applied: bool
    smoke_passed: bool


class ComparisonIdentity(BaseModel):
    """All protocol dimensions required by the plan before metrics may compare."""

    model_config = ConfigDict(extra="forbid")

    dataset_identity: str
    split_identity: str
    seed: int
    checkpoint_selection: str
    command_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metric_implementation_refs: list[str]
    evaluation_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outputs_complete: bool


def comparable(candidate: ComparisonIdentity | None, baseline: ComparisonIdentity | None) -> EvaluationStatus:
    if candidate is None or baseline is None or not candidate.outputs_complete or not baseline.outputs_complete:
        return "NON_COMPARABLE"
    return "COMPARABLE" if candidate == baseline else "NON_COMPARABLE"


def scientific_effect(
    *,
    candidate_metrics: dict[str, object] | None,
    baseline_metrics: dict[str, object] | None,
    contract: EvaluationContract | None,
    evaluation_status: EvaluationStatus,
    implementation_evidence: ImplementationEvidence | None,
    metrics_parsed: bool,
    protocol_intact: bool,
) -> tuple[ScientificEffect | None, float | None, dict[str, float]]:
    """Classify only a valid, comparable, finite result; otherwise say nothing."""
    if (
        contract is None
        or candidate_metrics is None
        or baseline_metrics is None
        or implementation_evidence is None
        or not implementation_evidence.patch_applied
        or not implementation_evidence.smoke_passed
        or not metrics_parsed
        or not protocol_intact
        or evaluation_status != "COMPARABLE"
    ):
        return None, None, {}
    def delta(metric_name: str) -> float | None:
        candidate = candidate_metrics.get(metric_name)
        baseline = baseline_metrics.get(metric_name)
        if not isinstance(candidate, (int, float)) or not isinstance(baseline, (int, float)):
            return None
        if not math.isfinite(candidate) or not math.isfinite(baseline):
            return None
        direction = next(metric.direction for metric in contract.metrics if metric.name == metric_name)
        raw = float(candidate) - float(baseline)
        return raw if direction == "maximize" else -raw
    deltas = {metric.name: delta(metric.name) for metric in contract.metrics}
    if any(value is None for value in deltas.values()):
        return "INCONCLUSIVE", None, {}
    primary = deltas[contract.primary_metric]
    assert primary is not None
    guardrails = {name: deltas[name] for name in contract.guardrails}
    if primary is None:
        return "INCONCLUSIVE", None, guardrails
    if primary > 0:
        return "IMPROVEMENT", primary, guardrails
    if primary < 0:
        return "REGRESSION", primary, guardrails
    return "NO_EFFECT", primary, guardrails
