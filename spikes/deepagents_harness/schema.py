"""Minimal AutoAD schemas for DeepAgentsHarness spike validation."""

from pydantic import BaseModel, ConfigDict


class ExperimentPlan(BaseModel):
    model_config = ConfigDict(extra="allow")

    experiment_goal: str
    baseline: str
    dataset: str
    categories: list[str]
    metrics: list[str]
    control_group: str
    experiment_group: str
    resource_budget: str
    risks: list[str]


class PatchPlan(BaseModel):
    model_config = ConfigDict(extra="allow")

    target_repo: str
    files_to_inspect: list[str]
    files_to_modify: list[str]
    planned_changes: list[str]
    expected_risks: list[str]
    requires_approval: bool
