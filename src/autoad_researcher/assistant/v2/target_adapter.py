"""Registry for deterministic repository target validation and job mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from autoad_researcher.repository_intelligence.workload_target import RepositoryWorkloadTarget


@dataclass(frozen=True)
class TargetAdapter:
    adapter_id: str
    selector_model: type[BaseModel]
    job_type: str
    evidence_role: str
    payload_key: str
    description: str

    def validate_selectors(self, selectors: dict[str, Any]) -> dict[str, Any]:
        return self.selector_model.model_validate(selectors).model_dump(mode="json")

    def prompt_entry(self) -> dict[str, Any]:
        schema = self.selector_model.model_json_schema()
        return {
            "adapter_id": self.adapter_id,
            "description": self.description,
            "selectors_schema": schema.get("properties", {}),
        }


@dataclass(frozen=True)
class ResolvedTarget:
    adapter_id: str
    selectors: dict[str, Any]
    job_type: str
    evidence_role: str
    payload_key: str


class TargetAdapterRegistry:
    def __init__(self, adapters: list[TargetAdapter] | None = None) -> None:
        self._adapters: dict[str, TargetAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: TargetAdapter) -> None:
        if not adapter.adapter_id.strip():
            raise ValueError("target adapter_id must not be empty")
        if adapter.adapter_id in self._adapters:
            raise ValueError(f"duplicate target adapter_id: {adapter.adapter_id}")
        self._adapters[adapter.adapter_id] = adapter

    def resolve(
        self,
        adapter_id: str,
        selectors: dict[str, Any],
    ) -> ResolvedTarget | None:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            return None
        try:
            validated = adapter.validate_selectors(selectors)
        except Exception:
            return None
        return ResolvedTarget(
            adapter_id=adapter.adapter_id,
            selectors=validated,
            job_type=adapter.job_type,
            evidence_role=adapter.evidence_role,
            payload_key=adapter.payload_key,
        )

    def prompt_catalog(self) -> list[dict[str, Any]]:
        return [self._adapters[key].prompt_entry() for key in sorted(self._adapters)]


_DEFAULT_TARGET_ADAPTER_REGISTRY = TargetAdapterRegistry([
    TargetAdapter(
        adapter_id="kernelbench",
        selector_model=RepositoryWorkloadTarget,
        job_type="repo_analyze",
        evidence_role="repo_acquired",
        payload_key="repository_target",
        description="KernelBench repository workload selected by level and problem_id.",
    ),
])


def get_target_adapter_registry() -> TargetAdapterRegistry:
    return _DEFAULT_TARGET_ADAPTER_REGISTRY
