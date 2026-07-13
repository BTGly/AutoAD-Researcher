"""Versioned semantic projections for intent confirmation and authorization."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from autoad_researcher.assistant.v2.intent_contract import ResearchIntentContract
from autoad_researcher.core.control_plane.hashing import domain_sha256


class _ContractSemanticPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_domain: str | None
    research_goal: str | None
    baseline: str | None
    baseline_repo: str | None
    baseline_commit: str | None
    baseline_entrypoint: str | None
    baseline_config: str | None
    dataset: str | None
    evaluation_protocol: str | None
    primary_metrics: list[str]
    secondary_metrics: list[str]
    metric_priority: str | None
    success_criteria: str | None
    compute_environment: dict[str, Any]
    execution_mode: str
    user_improvement_hints: list[str]
    user_target_module_hints: list[str]
    preferred_method_hints: list[str]
    risk_preference: str | None
    allowed_change_scope: list[str]
    forbidden_change_scope: list[str]


class ConfirmationDraftHashPayload(_ContractSemanticPayload):
    hash_schema: Literal["research_intent_confirmation_draft:v1"] = "research_intent_confirmation_draft:v1"


class ConfirmedContractHashPayload(_ContractSemanticPayload):
    hash_schema: Literal["research_intent_contract:v1"] = "research_intent_contract:v1"


def confirmation_draft_sha256(contract: ResearchIntentContract) -> str:
    return domain_sha256(
        "autoad:research_intent_confirmation_draft:v1",
        ConfirmationDraftHashPayload(**_semantic_values(contract)),
    )


def confirmed_contract_sha256(contract: ResearchIntentContract) -> str:
    return domain_sha256(
        "autoad:research_intent_contract:v1",
        ConfirmedContractHashPayload(**_semantic_values(contract)),
    )


def _semantic_values(contract: ResearchIntentContract) -> dict[str, Any]:
    return {
        "run_id": _required_string(contract.run_id),
        "task_domain": _optional_string(contract.task_domain),
        "research_goal": _optional_string(contract.research_goal),
        "baseline": _optional_string(contract.baseline),
        "baseline_repo": _optional_string(contract.baseline_repo),
        "baseline_commit": _optional_string(contract.baseline_commit),
        "baseline_entrypoint": _optional_string(contract.baseline_entrypoint),
        "baseline_config": _optional_string(contract.baseline_config),
        "dataset": _optional_string(contract.dataset),
        "evaluation_protocol": _optional_string(contract.evaluation_protocol),
        "primary_metrics": _ordered_unique(contract.primary_metrics),
        "secondary_metrics": _ordered_unique(contract.secondary_metrics),
        "metric_priority": _optional_string(contract.metric_priority),
        "success_criteria": _optional_string(contract.success_criteria),
        "compute_environment": _normalize_mapping(contract.compute_environment),
        "execution_mode": _required_string(contract.execution_mode),
        "user_improvement_hints": _ordered_unique(contract.user_improvement_hints),
        "user_target_module_hints": _ordered_unique(contract.user_target_module_hints),
        "preferred_method_hints": _ordered_unique(contract.preferred_method_hints),
        "risk_preference": _optional_string(contract.risk_preference),
        "allowed_change_scope": sorted(set(_ordered_unique(contract.allowed_change_scope))),
        "forbidden_change_scope": sorted(set(_ordered_unique(contract.forbidden_change_scope))),
    }


def _required_string(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("required semantic contract string must not be empty")
    return normalized


def _optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _normalize_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key).strip(): _normalize_value(item) for key, item in value.items() if str(key).strip()}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _normalize_mapping(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value
