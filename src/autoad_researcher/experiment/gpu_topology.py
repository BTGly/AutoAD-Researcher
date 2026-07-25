"""Evidence-driven GPU topology planning.

GPU count is capacity. It is not, by itself, an instruction to split work
between baseline and candidate. The caller must supply the agent's explicit
topology conclusion and the evidence behind it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.experiment.gpu import GpuDevice


GpuTopologyKind = Literal["single_gpu", "ddp_multi_gpu", "model_parallel", "unknown"]
GpuExecutionMode = Literal[
    "single_attempt_multi_gpu",
    "independent_attempts_parallel",
    "independent_attempts_sequential",
    "paused_unknown",
]
GpuRole = Literal["baseline", "candidate"]


class GpuTopologyObservation(BaseModel):
    """Agent/preflight output describing what the repository actually uses."""

    model_config = ConfigDict(extra="forbid")

    topology_kind: GpuTopologyKind
    devices: list[GpuDevice] = Field(default_factory=list)
    requested_device_count: int = Field(default=0, ge=0)
    world_size: int | None = Field(default=None, ge=1)
    launch_method: str | None = None
    independent_roles_confirmed: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_summary: str = Field(min_length=1)


class GpuRoleAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: GpuRole
    device_ids: list[str] = Field(min_length=1)
    gpu_uuids: dict[str, str | None] = Field(default_factory=dict)
    seed: int | None = None


class GpuExecutionPlan(BaseModel):
    """Frozen execution topology consumed by Attempt creation and audit."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["ready", "unknown", "blocked"]
    topology_kind: GpuTopologyKind
    execution_mode: GpuExecutionMode
    world_size: int | None = Field(default=None, ge=1)
    launch_method: str | None = None
    assignments: list[GpuRoleAssignment] = Field(default_factory=list)
    swap_roles_by_seed: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


def plan_gpu_execution(
    observation: GpuTopologyObservation,
    *,
    swap_roles_by_seed: bool = False,
) -> GpuExecutionPlan:
    """Translate explicit repository evidence into a non-ambiguous topology."""

    devices = observation.devices
    if observation.topology_kind in {"ddp_multi_gpu", "model_parallel"}:
        if observation.requested_device_count < 2 or len(devices) < observation.requested_device_count:
            return GpuExecutionPlan(
                status="blocked",
                topology_kind=observation.topology_kind,
                execution_mode="paused_unknown",
                world_size=observation.world_size,
                launch_method=observation.launch_method,
                evidence_ids=observation.evidence_ids,
                rationale="multi-GPU topology was identified but the requested or observed device count is incomplete",
            )
        selected = devices[: observation.requested_device_count]
        return GpuExecutionPlan(
            status="ready",
            topology_kind=observation.topology_kind,
            execution_mode="single_attempt_multi_gpu",
            world_size=observation.world_size or observation.requested_device_count,
            launch_method=observation.launch_method,
            assignments=[GpuRoleAssignment(
                role="baseline",
                device_ids=[item.device_id for item in selected],
                gpu_uuids={item.device_id: item.gpu_uuid for item in selected},
            )],
            evidence_ids=observation.evidence_ids,
            rationale="repository evidence declares one multi-GPU training attempt",
        )

    if observation.topology_kind == "single_gpu" and observation.independent_roles_confirmed:
        if len(devices) < 2:
            return GpuExecutionPlan(
                status="blocked",
                topology_kind="single_gpu",
                execution_mode="paused_unknown",
                evidence_ids=observation.evidence_ids,
                rationale="independent baseline/candidate roles require two observed devices",
            )
        return GpuExecutionPlan(
            status="ready",
            topology_kind="single_gpu",
            execution_mode="independent_attempts_parallel",
            world_size=1,
            launch_method=observation.launch_method,
            assignments=[
                GpuRoleAssignment(role="baseline", device_ids=[devices[0].device_id], gpu_uuids={devices[0].device_id: devices[0].gpu_uuid}),
                GpuRoleAssignment(role="candidate", device_ids=[devices[1].device_id], gpu_uuids={devices[1].device_id: devices[1].gpu_uuid}),
            ],
            swap_roles_by_seed=swap_roles_by_seed,
            evidence_ids=observation.evidence_ids,
            rationale="agent evidence explicitly confirmed independent single-GPU roles",
        )

    return GpuExecutionPlan(
        status="unknown",
        topology_kind="unknown",
        execution_mode="paused_unknown",
        evidence_ids=observation.evidence_ids,
        rationale="GPU count is known only as capacity; repository training topology remains unresolved",
    )
