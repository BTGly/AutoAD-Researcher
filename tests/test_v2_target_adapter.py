from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.target_adapter import (
    TargetAdapter,
    TargetAdapterRegistry,
)


class DemoSelectors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite: str = Field(min_length=1)
    workload_id: int = Field(ge=0)


def test_registry_adds_benchmark_without_changing_dialogue_schema():
    registry = TargetAdapterRegistry([
        TargetAdapter(
            adapter_id="demo_suite",
            selector_model=DemoSelectors,
            job_type="repo_analyze",
            evidence_role="repo_acquired",
            payload_key="demo_target",
            description="Demo workload selector.",
        )
    ])

    resolved = registry.resolve(
        "demo_suite",
        {"suite": "operators", "workload_id": 7},
    )

    assert resolved is not None
    assert resolved.selectors == {"suite": "operators", "workload_id": 7}
    assert resolved.payload_key == "demo_target"
    assert registry.prompt_catalog()[0]["adapter_id"] == "demo_suite"
    assert set(registry.prompt_catalog()[0]["selectors_schema"]) == {"suite", "workload_id"}


def test_registry_rejects_unknown_adapter_and_invalid_selectors():
    registry = TargetAdapterRegistry()
    adapter = TargetAdapter(
        adapter_id="demo_suite",
        selector_model=DemoSelectors,
        job_type="repo_analyze",
        evidence_role="repo_acquired",
        payload_key="demo_target",
        description="Demo workload selector.",
    )
    registry.register(adapter)

    assert registry.resolve("missing", {"suite": "operators", "workload_id": 7}) is None
    assert registry.resolve("demo_suite", {"suite": "operators", "workload_id": -1}) is None
    assert registry.resolve(
        "demo_suite",
        {"suite": "operators", "workload_id": 7, "extra": True},
    ) is None
